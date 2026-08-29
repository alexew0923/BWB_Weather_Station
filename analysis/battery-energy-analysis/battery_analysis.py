"""Observed and derived battery analytics for the standalone battery project."""

import argparse
import contextlib
import io
import json
import math
import os
import sys
from datetime import timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

# This project remains a sibling of reliability-audit rather than turning that
# project into a general package. Narrow reuse is limited to its stable,
# behavior-verified validation, operating-schedule, gap, and daily primitives.
# Resolving from __file__ makes execution independent of the caller's cwd.
PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_RELIABILITY_PROJECT = PROJECT_DIR.parent / "reliability-audit"
if str(DEFAULT_RELIABILITY_PROJECT) not in sys.path:
    sys.path.insert(0, str(DEFAULT_RELIABILITY_PROJECT))

from audit_config import (
    NOMINAL_CYCLE_MINUTES,
    PLAUSIBLE_SENSOR_RANGES,
    STATION_TIMEZONE,
    active_window_for,
    regime_for,
)
from outage_analysis import compute_gaps, detect_outages, real_outages, significant_outages
from reliability_metrics import (
    add_daily_gap_stats,
    add_slot_index,
    compute_daily_row_completeness,
)
from data_validation import load_and_validate_data, localize_timestamps


BATTERY_COLUMN = "Battery Voltage"
BATTERY_MV_LOW, BATTERY_MV_HIGH = PLAUSIBLE_SENSOR_RANGES[BATTERY_COLUMN]
MIN_TREND_SAMPLES = 12
MIN_TREND_COVERAGE = 0.80
DEFAULT_OUTAGE_WINDOWS_HOURS = (6, 12, 24)


def valid_battery_mask(df):
    """Rows with populated battery mV inside the audit's existing broad bounds."""
    values = df[BATTERY_COLUMN]
    # Zero never occurs in the historical export and its sentinel semantics are
    # undocumented. It is excluded rather than interpreted as a real 0 V cell.
    return values.notna() & (values > BATTERY_MV_LOW) & (values <= BATTERY_MV_HIGH)


def _battery_rows(df):
    rows = df.loc[valid_battery_mask(df), [
        "timestamp", BATTERY_COLUMN, "Temperature", "Humidity", "Rain Value",
    ]].copy()
    rows["battery_voltage_v"] = rows[BATTERY_COLUMN] / 1000.0
    return rows.sort_values("timestamp").reset_index(drop=True)


def battery_data_quality(df):
    """Describe validity and commissioning without treating old nulls as faults."""
    valid = _battery_rows(df)
    populated = df[BATTERY_COLUMN].notna()
    rejected = populated & ~valid_battery_mask(df)
    zero_count = int((df[BATTERY_COLUMN] == 0).sum())

    if valid.empty:
        return {
            "status": "unavailable",
            "first_valid_timestamp": None,
            "last_valid_timestamp": None,
            "valid_reading_count": 0,
            "completeness_since_commissioning": None,
            "missing_since_commissioning": None,
            "rejected_reading_count": int(rejected.sum()),
            "zero_reading_count": zero_count,
            "observed_min_voltage_v": None,
            "observed_max_voltage_v": None,
            "validation_bounds_v": [BATTERY_MV_LOW / 1000.0, BATTERY_MV_HIGH / 1000.0],
            "zero_semantics": "not determinable; zero is excluded from valid readings",
        }

    first = valid["timestamp"].iloc[0]
    commissioned_rows = df[df["timestamp"] >= first]
    commissioned_valid = valid_battery_mask(commissioned_rows)
    return {
        "status": "available",
        "first_valid_timestamp": first.isoformat(),
        "last_valid_timestamp": valid["timestamp"].iloc[-1].isoformat(),
        "valid_reading_count": len(valid),
        "completeness_since_commissioning": round(
            float(commissioned_valid.mean()), 6
        ),
        "missing_since_commissioning": int(commissioned_rows[BATTERY_COLUMN].isna().sum()),
        "rejected_reading_count": int(rejected.loc[commissioned_rows.index].sum()),
        "zero_reading_count": zero_count,
        "observed_min_voltage_v": round(float(valid["battery_voltage_v"].min()), 6),
        "observed_max_voltage_v": round(float(valid["battery_voltage_v"].max()), 6),
        "validation_bounds_v": [BATTERY_MV_LOW / 1000.0, BATTERY_MV_HIGH / 1000.0],
        "zero_semantics": (
            "no zero readings observed; special/sentinel meaning cannot be inferred"
            if zero_count == 0 else
            "zero is excluded because the historical schema does not document its meaning"
        ),
    }


def fit_voltage_slope(rows, min_samples=MIN_TREND_SAMPLES, min_span_hours=0.0):
    """OLS voltage slope in V/hour, requiring a non-trivial sample set."""
    clean = rows.dropna(subset=["timestamp", "battery_voltage_v"]).sort_values("timestamp")
    if len(clean) < min_samples:
        return math.nan
    utc = clean["timestamp"].dt.tz_convert("UTC")
    x = (utc - utc.iloc[0]).dt.total_seconds().to_numpy() / 3600.0
    span = float(x[-1])
    if span <= 0 or span < min_span_hours:
        return math.nan
    y = clean["battery_voltage_v"].to_numpy(dtype=float)
    return float(np.polyfit(x, y, 1)[0])


def _daily_battery_record(day, rows, full_df, min_trend_samples):
    result = {
        "date": day,
        "battery_first_v": math.nan,
        "battery_last_v": math.nan,
        "battery_min_v": math.nan,
        "battery_max_v": math.nan,
        "battery_mean_v": math.nan,
        "battery_median_v": math.nan,
        "battery_range_v": math.nan,
        "battery_net_change_v": math.nan,
        "battery_daily_slope_v_per_day": math.nan,
        "battery_valid_samples": 0,
        "battery_sample_span_hours": math.nan,
        "battery_min_timestamp": None,
        "battery_max_timestamp": None,
        "daytime_voltage_change_v": math.nan,
        "scheduled_inactive_voltage_change_v": math.nan,
        "voltage_behavior_proxy": None,
        "temperature_mean_c": math.nan,
        "temperature_min_c": math.nan,
    }
    if rows.empty:
        return result

    voltage = rows["battery_voltage_v"]
    first, last = rows.iloc[0], rows.iloc[-1]
    span_hours = (
        last["timestamp"].tz_convert("UTC") - first["timestamp"].tz_convert("UTC")
    ).total_seconds() / 3600.0
    slope = fit_voltage_slope(rows, min_samples=min_trend_samples)
    net = float(last["battery_voltage_v"] - first["battery_voltage_v"])
    min_row = rows.loc[voltage.idxmin()]
    max_row = rows.loc[voltage.idxmax()]
    result.update({
        "battery_first_v": float(first["battery_voltage_v"]),
        "battery_last_v": float(last["battery_voltage_v"]),
        "battery_min_v": float(voltage.min()),
        "battery_max_v": float(voltage.max()),
        "battery_mean_v": float(voltage.mean()),
        "battery_median_v": float(voltage.median()),
        "battery_range_v": float(voltage.max() - voltage.min()),
        "battery_net_change_v": net,
        "battery_daily_slope_v_per_day": slope * 24 if not math.isnan(slope) else math.nan,
        "battery_valid_samples": len(rows),
        "battery_sample_span_hours": span_hours,
        "battery_min_timestamp": min_row["timestamp"].isoformat(),
        "battery_max_timestamp": max_row["timestamp"].isoformat(),
        "temperature_mean_c": float(rows["Temperature"].mean()),
        "temperature_min_c": float(rows["Temperature"].min()),
    })

    if not math.isnan(slope):
        if slope > 0 and net > 0:
            result["voltage_behavior_proxy"] = "charging-like (positive voltage slope)"
        elif slope < 0 and net < 0:
            result["voltage_behavior_proxy"] = "discharging-like (negative voltage slope)"
        else:
            result["voltage_behavior_proxy"] = "mixed voltage behavior"

    regime = regime_for(day)
    if regime.active_start_hour > 0 or regime.active_end_hour < 24:
        open_at, close_at = active_window_for(day)
        active = rows[
            (rows["timestamp"] >= open_at) & (rows["timestamp"] < close_at)
        ]
        if len(active) >= 2:
            result["daytime_voltage_change_v"] = float(
                active["battery_voltage_v"].iloc[-1] - active["battery_voltage_v"].iloc[0]
            )

        next_open, _ = active_window_for(day + timedelta(days=1))
        tolerance = pd.Timedelta(minutes=3 * NOMINAL_CYCLE_MINUTES)
        all_valid = _battery_rows(full_df)
        before_close = all_valid[
            (all_valid["timestamp"] <= close_at)
            & (all_valid["timestamp"] >= close_at - tolerance)
        ]
        after_open = all_valid[
            (all_valid["timestamp"] >= next_open)
            & (all_valid["timestamp"] <= next_open + tolerance)
        ]
        if not before_close.empty and not after_open.empty:
            result["scheduled_inactive_voltage_change_v"] = float(
                after_open["battery_voltage_v"].iloc[0]
                - before_close["battery_voltage_v"].iloc[-1]
            )
    return result


def compute_daily_battery_metrics(
    df, outages=None, min_trend_samples=MIN_TREND_SAMPLES,
    reliability_daily=None,
):
    """One calendar row per day from battery commissioning through dataset end."""
    quality = battery_data_quality(df)
    columns = [
        "date", "battery_first_v", "battery_last_v", "battery_min_v",
        "battery_max_v", "battery_mean_v", "battery_median_v", "battery_range_v",
        "battery_net_change_v", "battery_daily_slope_v_per_day",
        "battery_valid_samples", "battery_sample_span_hours", "battery_min_timestamp",
        "battery_max_timestamp", "daytime_voltage_change_v",
        "scheduled_inactive_voltage_change_v", "voltage_behavior_proxy",
        "temperature_mean_c", "temperature_min_c", "battery_completeness",
        "telemetry_completeness", "significant_gap_count", "largest_gap_minutes",
        "rolling_72h_slope_v_per_day",
    ]
    if quality["status"] != "available":
        return pd.DataFrame(columns=columns)

    valid = _battery_rows(df)
    start_day = valid["timestamp"].iloc[0].date()
    end_day = df["timestamp"].iloc[-1].date()
    records = []
    day = start_day
    while day <= end_day:
        rows = valid[valid["timestamp"].dt.date == day]
        records.append(_daily_battery_record(day, rows, df, min_trend_samples))
        day += timedelta(days=1)
    daily = pd.DataFrame(records)

    reliability = (
        reliability_daily.copy()
        if reliability_daily is not None else compute_daily_row_completeness(df)
    )
    if outages is None:
        indexed = add_slot_index(df) if "slot" not in df.columns else df
        outages = detect_outages(indexed, compute_gaps(indexed))
    if "largest_gap_minutes" not in reliability.columns:
        reliability = add_daily_gap_stats(reliability, outages)
    if "row_completeness" not in reliability.columns:
        raise ValueError("Reliability daily input lacks row_completeness")
    significant = significant_outages(outages)
    gap_counts = (
        significant.groupby(significant["gap_start"].dt.date).size()
        if not significant.empty else pd.Series(dtype=int)
    )
    received = df.groupby(df["timestamp"].dt.date).size()
    daily = daily.merge(
        reliability[["date", "row_completeness", "largest_gap_minutes"]],
        on="date", how="left",
    ).rename(columns={"row_completeness": "telemetry_completeness"})
    daily["significant_gap_count"] = daily["date"].map(gap_counts).fillna(0).astype(int)
    daily["received_rows"] = daily["date"].map(received).fillna(0).astype(int)
    daily["battery_completeness"] = np.where(
        daily["received_rows"] > 0,
        daily["battery_valid_samples"] / daily["received_rows"],
        np.nan,
    )

    rolling = compute_rolling_battery_metrics(df, min_trend_samples)
    if not rolling.empty:
        last_daily = rolling.groupby(rolling["timestamp"].dt.date).tail(1).copy()
        last_daily["date"] = last_daily["timestamp"].dt.date
        last_daily = last_daily.rename(columns={
            "rolling_slope_72h_v_per_day": "rolling_72h_slope_v_per_day"
        })
        daily = daily.merge(
            last_daily[["date", "rolling_72h_slope_v_per_day"]], on="date", how="left"
        )
    else:
        daily["rolling_slope_72h_v_per_day"] = np.nan
    return daily[columns]


def _rolling_slope(frame, window, min_samples):
    indexed = frame.set_index("timestamp")
    y = indexed["battery_voltage_v"]
    utc_index = indexed.index.tz_convert("UTC")
    x = pd.Series(
        (utc_index - utc_index[0]).total_seconds() / 3600.0,
        index=indexed.index,
    )
    rolling = lambda series: series.rolling(window, min_periods=1)
    n = rolling(y).count()
    sx = rolling(x).sum()
    sy = rolling(y).sum()
    sxy = rolling(x * y).sum()
    sx2 = rolling(x * x).sum()
    denominator = n * sx2 - sx * sx
    slope = (n * sxy - sx * sy) / denominator
    window_hours = pd.Timedelta(window).total_seconds() / 3600.0
    coverage = x - rolling(x).min()
    return slope.where(
        (n >= min_samples)
        & (coverage >= MIN_TREND_COVERAGE * window_hours)
        & (denominator > 0)
    )


def compute_rolling_battery_metrics(df, min_trend_samples=MIN_TREND_SAMPLES):
    """Sample-level rolling statistics; trends need 12 samples and 80% coverage."""
    valid = _battery_rows(df)
    columns = [
        "timestamp", "battery_voltage_v", "voltage_change_24h_v",
        "voltage_change_72h_v", "rolling_mean_24h_v", "rolling_min_24h_v",
        "rolling_max_24h_v", "rolling_slope_24h_v_per_hour",
        "rolling_slope_72h_v_per_day",
    ]
    if valid.empty:
        return pd.DataFrame(columns=columns)
    indexed = valid.set_index("timestamp")
    voltage = indexed["battery_voltage_v"]
    output = valid[["timestamp", "battery_voltage_v"]].copy().set_index("timestamp")
    for hours in (24, 72):
        window = f"{hours}h"
        roll = voltage.rolling(window, min_periods=1)
        first = roll.apply(lambda values: values[0], raw=True)
        utc_index = indexed.index.tz_convert("UTC")
        elapsed_hours = pd.Series(
            (utc_index - utc_index[0]).total_seconds() / 3600.0,
            index=indexed.index,
        )
        coverage = elapsed_hours - elapsed_hours.rolling(window).min()
        output[f"voltage_change_{hours}h_v"] = (voltage - first).where(
            (roll.count() >= min_trend_samples)
            & (coverage >= MIN_TREND_COVERAGE * hours)
        )
    output["rolling_mean_24h_v"] = voltage.rolling("24h", min_periods=min_trend_samples).mean()
    output["rolling_min_24h_v"] = voltage.rolling("24h", min_periods=min_trend_samples).min()
    output["rolling_max_24h_v"] = voltage.rolling("24h", min_periods=min_trend_samples).max()
    output["rolling_slope_24h_v_per_hour"] = _rolling_slope(
        valid, "24h", min_trend_samples
    )
    output["rolling_slope_72h_v_per_day"] = _rolling_slope(
        valid, "72h", min_trend_samples
    ) * 24
    return output.reset_index()[columns]


def compute_outage_battery_context(
    df, outages, windows_hours=DEFAULT_OUTAGE_WINDOWS_HOURS,
    min_trend_samples=MIN_TREND_SAMPLES,
):
    """Battery observations around each significant outage, never causal labels."""
    valid = _battery_rows(df)
    significant = significant_outages(outages).reset_index(drop=True)
    records = []
    for index, outage in significant.iterrows():
        start, end = outage["gap_start"], outage["gap_end"]
        record = {
            "outage_number": index + 1,
            "outage_start": start,
            "outage_end": end,
            "gap_minutes": outage["gap_minutes"],
            "severity": outage["severity"],
            "missed_transmissions": outage["missed_transmissions"],
        }
        usable = False
        for hours in windows_hours:
            window_start = (start.tz_convert("UTC") - pd.Timedelta(hours=hours)).tz_convert(STATION_TIMEZONE)
            before = valid[(valid["timestamp"] >= window_start) & (valid["timestamp"] <= start)]
            prefix = f"pre_{hours}h"
            record[f"{prefix}_valid_samples"] = len(before)
            record[f"{prefix}_latest_voltage_v"] = (
                float(before["battery_voltage_v"].iloc[-1]) if not before.empty else math.nan
            )
            record[f"{prefix}_minimum_voltage_v"] = (
                float(before["battery_voltage_v"].min()) if not before.empty else math.nan
            )
            record[f"{prefix}_voltage_change_v"] = (
                float(before["battery_voltage_v"].iloc[-1] - before["battery_voltage_v"].iloc[0])
                if len(before) >= 2 else math.nan
            )
            span = (
                (before["timestamp"].iloc[-1].tz_convert("UTC")
                 - before["timestamp"].iloc[0].tz_convert("UTC")).total_seconds() / 3600.0
                if len(before) >= 2 else math.nan
            )
            record[f"{prefix}_sample_span_hours"] = span
            record[f"{prefix}_slope_v_per_hour"] = fit_voltage_slope(
                before, min_trend_samples, MIN_TREND_COVERAGE * hours
            )
            if hours == max(windows_hours) and len(before) >= min_trend_samples:
                usable = True

        post_end = (end.tz_convert("UTC") + pd.Timedelta(hours=6)).tz_convert(STATION_TIMEZONE)
        after = valid[(valid["timestamp"] >= end) & (valid["timestamp"] <= post_end)]
        record["post_6h_valid_samples"] = len(after)
        record["first_recovery_voltage_v"] = (
            float(after["battery_voltage_v"].iloc[0]) if not after.empty else math.nan
        )
        record["post_6h_voltage_change_v"] = (
            float(after["battery_voltage_v"].iloc[-1] - after["battery_voltage_v"].iloc[0])
            if len(after) >= 2 else math.nan
        )
        record["usable_pre_outage_context"] = usable

        best_hours = max(windows_hours)
        change = record[f"pre_{best_hours}h_voltage_change_v"]
        if not math.isnan(change):
            record["observed"] = (
                f"Battery voltage changed {change:+.3f} V in the available "
                f"{best_hours} h pre-outage window."
            )
            record["suggestive"] = (
                "A decline may be consistent with worsening power conditions, but the pattern is not causal evidence."
                if change < 0 else
                "The observed voltage pattern does not by itself identify the outage mechanism."
            )
        else:
            record["observed"] = "No usable battery trend was available before this outage."
            record["suggestive"] = "No battery-related inference is supported for this outage."
        record["not_proven"] = (
            "Battery or power failure is not established as the root cause; the delivery path remains ambiguous."
        )
        records.append(record)
    return pd.DataFrame(records)


def _correlation_record(category, x_name, y_name, x, y):
    paired = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(paired) < 10 or paired["x"].nunique() < 2 or paired["y"].nunique() < 2:
        return {
            "category": category, "x": x_name, "y": y_name,
            "sample_size": len(paired), "pearson_r": None, "spearman_rho": None,
            "p_value": None, "interpretation": "insufficient independent variation",
        }
    pearson = float(paired["x"].corr(paired["y"], method="pearson"))
    # Spearman is Pearson on ranks. Computing it directly avoids adding SciPy
    # solely for one descriptive statistic.
    spearman = float(paired["x"].rank().corr(paired["y"].rank()))
    magnitude = abs(spearman)
    strength = "negligible" if magnitude < 0.2 else "weak" if magnitude < 0.4 else "moderate" if magnitude < 0.6 else "strong"
    direction = "positive" if spearman > 0 else "negative"
    return {
        "category": category, "x": x_name, "y": y_name,
        "sample_size": len(paired), "pearson_r": round(pearson, 6),
        "spearman_rho": round(spearman, 6), "p_value": None,
        "interpretation": (
            f"{strength} {direction} day-level association; not causal. "
            "No p-value is reported because daily observations are serially dependent."
        ),
    }


def analyze_relationships(daily):
    """Descriptive day-level reliability and temperature associations."""
    if daily.empty:
        return []
    outage_occurrence = (daily["significant_gap_count"] > 0).astype(int)
    pairs = [
        ("reliability", "daily minimum battery voltage", "telemetry completeness",
         daily["battery_min_v"], daily["telemetry_completeness"]),
        ("reliability", "daily mean battery voltage", "telemetry completeness",
         daily["battery_mean_v"], daily["telemetry_completeness"]),
        ("reliability", "daily net battery voltage change", "telemetry completeness",
         daily["battery_net_change_v"], daily["telemetry_completeness"]),
        ("reliability", "rolling 72h voltage slope", "significant gap count",
         daily["rolling_72h_slope_v_per_day"], daily["significant_gap_count"]),
        ("reliability", "latest daily battery voltage", "significant outage occurrence",
         daily["battery_last_v"], outage_occurrence),
        ("environment", "daily battery voltage change", "mean temperature",
         daily["battery_net_change_v"], daily["temperature_mean_c"]),
        ("environment", "daily minimum battery voltage", "minimum temperature",
         daily["battery_min_v"], daily["temperature_min_c"]),
        ("environment", "daytime voltage change", "mean temperature",
         daily["daytime_voltage_change_v"], daily["temperature_mean_c"]),
    ]
    return [_correlation_record(*pair) for pair in pairs]


def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if math.isnan(float(value)) or math.isinf(float(value)) else float(value)
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    return value


def build_battery_summary(df, daily, outage_context, relationships):
    quality = battery_data_quality(df)
    if quality["status"] != "available":
        return {
            "status": "skipped",
            "reason": "No valid battery telemetry is available.",
            "data_quality": quality,
            "quantity_categories": {
                "observed": "direct telemetry values",
                "derived": "statistics calculated from telemetry",
                "modeled": "only produced when hardware parameters are supplied",
            },
        }

    robust = daily[
        (daily["battery_valid_samples"] >= MIN_TREND_SAMPLES)
        & (daily["battery_sample_span_hours"] >= 6)
    ]
    positive = robust.nlargest(1, "battery_net_change_v")
    negative = robust.nsmallest(1, "battery_net_change_v")
    usable_relationships = [
        item for item in relationships
        if item["category"] == "reliability" and item["spearman_rho"] is not None
    ]
    strongest = max(usable_relationships, key=lambda item: abs(item["spearman_rho"])) if usable_relationships else None
    usable_outages = int(outage_context["usable_pre_outage_context"].sum()) if not outage_context.empty else 0

    def daily_extreme(frame):
        if frame.empty:
            return None
        row = frame.iloc[0]
        return {"date": str(row["date"]), "net_voltage_change_v": float(row["battery_net_change_v"])}

    return _json_safe({
        "status": "complete",
        "quantity_categories": {
            "observed": "battery voltage and environmental telemetry",
            "derived": "daily metrics, rolling trends, outage context, and correlations",
            "modeled": "energy budget only when explicit hardware parameters are supplied",
        },
        "data_quality": quality,
        "derived_summary": {
            "typical_daily_voltage_range_v": float(robust["battery_range_v"].median()) if not robust.empty else None,
            "median_daily_net_voltage_change_v": float(robust["battery_net_change_v"].median()) if not robust.empty else None,
            "strongest_daily_positive_change": daily_extreme(positive),
            "strongest_daily_negative_change": daily_extreme(negative),
            "significant_outages_analyzed": len(outage_context),
            "outages_with_usable_pre_outage_battery_context": usable_outages,
            "strongest_observed_reliability_relationship": strongest,
            "trend_minimum_rule": (
                f"At least {MIN_TREND_SAMPLES} valid readings and "
                f"{MIN_TREND_COVERAGE:.0%} coverage of rolling/outage windows."
            ),
        },
        "environment_scope": (
            "Temperature relationships are reported. Humidity and raw rain/wetness are omitted "
            "from headline interpretation because mechanisms and field semantics are ambiguous."
        ),
        "limitations": [
            "Battery voltage is not battery percentage, state of charge, stored energy, or battery health.",
            "Battery chemistry, nominal capacity, divider calibration, charging circuit, load current, and panel specifications are undocumented here.",
            "Voltage reflects nonlinear state-of-charge behavior, load, charging, temperature, and measurement effects.",
            "Telemetry gaps hide battery behavior during the very outages being investigated.",
            "Day-level observations are serially dependent; correlations are descriptive and not causal.",
            "Historical configuration or field-semantic changes cannot be ruled out from the CSV alone.",
        ],
        "hardware_parameters_needed_for_calibration": [
            "battery chemistry and nominal capacity (mAh)",
            "battery-voltage measurement circuit and calibration",
            "active and sleep current draw (mA)",
            "active and sleep duration per cycle",
            "sensor and radio current contributions",
            "solar-panel rated power and orientation",
            "equivalent sun hours and charging/system efficiency",
        ],
    })


def _plot_battery_voltage(rolling, outages, output_dir):
    plotted = rolling.copy()
    breaks = plotted["timestamp"].diff().dt.total_seconds() > 6 * NOMINAL_CYCLE_MINUTES * 60
    plotted.loc[breaks, ["battery_voltage_v", "rolling_mean_24h_v"]] = np.nan
    fig, ax = plt.subplots(figsize=(15, 5.5))
    ax.plot(plotted["timestamp"], plotted["battery_voltage_v"], color="#78909c", alpha=0.25, linewidth=0.6, label="observed voltage")
    ax.plot(plotted["timestamp"], plotted["rolling_mean_24h_v"], color="#ef6c00", linewidth=1.7, label="24 h rolling mean")
    battery_start = rolling["timestamp"].min()
    relevant = significant_outages(outages)
    relevant = relevant[relevant["gap_start"] >= battery_start]
    for moment in relevant.nlargest(10, "gap_minutes")["gap_start"]:
        ax.axvline(moment, color="#c62828", linewidth=0.7, alpha=0.35)
    ax.set_title("Observed battery voltage and 24-hour rolling mean\nred lines mark the ten longest significant outages during battery availability")
    ax.set_ylabel("battery voltage (V)")
    ax.set_xlabel(f"time ({STATION_TIMEZONE.key})")
    ax.legend(fontsize=8)
    ax.margins(x=0.01)
    fig.tight_layout()
    path = os.path.join(output_dir, "plot_battery_voltage.png")
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def _plot_daily_profile(daily, output_dir):
    # Keep calendar rows with NaN battery metrics so outages appear as genuine
    # breaks rather than straight lines joining observations across missing time.
    dates = pd.to_datetime(daily["date"])
    fig, ax = plt.subplots(figsize=(15, 5.5))
    ax.fill_between(dates, daily["battery_min_v"], daily["battery_max_v"], color="#ffcc80", alpha=0.45, label="daily min-max")
    ax.plot(dates, daily["battery_mean_v"], color="#e65100", linewidth=1.5, label="daily mean")
    ax.set_title("Daily observed battery-voltage profile")
    ax.set_ylabel("battery voltage (V)")
    ax.set_xlabel("date")
    ax.legend(fontsize=8)
    ax.margins(x=0.01)
    fig.tight_layout()
    path = os.path.join(output_dir, "plot_battery_daily_profile.png")
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def _plot_reliability_relationship(daily, output_dir):
    usable = daily.dropna(subset=["battery_min_v", "telemetry_completeness"])
    fig, ax = plt.subplots(figsize=(7.5, 5.5))
    scatter = ax.scatter(usable["battery_min_v"], 100 * usable["telemetry_completeness"], c=usable["significant_gap_count"], cmap="magma", s=30, alpha=0.75)
    ax.set_title("Daily minimum battery voltage vs telemetry completeness\ndescriptive association only")
    ax.set_xlabel("daily minimum battery voltage (V)")
    ax.set_ylabel("telemetry completeness (%)")
    fig.colorbar(scatter, ax=ax, label="significant gaps starting that day")
    fig.tight_layout()
    path = os.path.join(output_dir, "plot_battery_reliability_relationship.png")
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def _plot_outage_context(outage_context, output_dir):
    usable = outage_context.dropna(subset=["pre_24h_latest_voltage_v"])
    fig, ax = plt.subplots(figsize=(7.5, 5.5))
    ax.scatter(usable["pre_24h_latest_voltage_v"], usable["gap_minutes"] / 60.0, color="#c62828", alpha=0.7, s=28)
    ax.set_yscale("log")
    ax.set_title("Latest pre-outage battery voltage vs outage duration\ndescriptive context; no root-cause inference")
    ax.set_xlabel("latest voltage in prior 24 h (V)")
    ax.set_ylabel("outage duration (hours, log scale)")
    fig.tight_layout()
    path = os.path.join(output_dir, "plot_battery_outage_context.png")
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def run_battery_analysis(
    df, output_dir, outages=None, min_trend_samples=MIN_TREND_SAMPLES,
    outage_windows_hours=DEFAULT_OUTAGE_WINDOWS_HOURS, reliability_daily=None,
):
    """Run the optional battery workflow without changing the main audit."""
    os.makedirs(output_dir, exist_ok=True)
    quality = battery_data_quality(df)
    if outages is None:
        indexed = add_slot_index(df) if "slot" not in df.columns else df
        outages = detect_outages(indexed, compute_gaps(indexed))

    if quality["status"] != "available":
        summary = build_battery_summary(df, pd.DataFrame(), pd.DataFrame(), [])
        summary_path = os.path.join(output_dir, "battery_summary.json")
        with open(summary_path, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2, sort_keys=True)
            handle.write("\n")
        return summary, [summary_path]

    daily = compute_daily_battery_metrics(
        df, outages, min_trend_samples, reliability_daily=reliability_daily
    )
    rolling = compute_rolling_battery_metrics(df, min_trend_samples)
    outage_context = compute_outage_battery_context(
        df, outages, outage_windows_hours, min_trend_samples
    )
    relationships = analyze_relationships(daily)
    summary = build_battery_summary(df, daily, outage_context, relationships)

    daily_path = os.path.join(output_dir, "battery_daily_metrics.csv")
    rolling_path = os.path.join(output_dir, "battery_rolling_metrics.csv")
    outage_path = os.path.join(output_dir, "battery_outage_context.csv")
    relationship_path = os.path.join(output_dir, "battery_relationships.csv")
    summary_path = os.path.join(output_dir, "battery_summary.json")
    daily.to_csv(daily_path, index=False)
    rolling.to_csv(rolling_path, index=False)
    outage_context.to_csv(outage_path, index=False)
    pd.DataFrame(relationships).to_csv(relationship_path, index=False)
    with open(summary_path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")

    written = [daily_path, rolling_path, outage_path, relationship_path, summary_path]
    written.extend([
        _plot_battery_voltage(rolling, outages, output_dir),
        _plot_daily_profile(daily, output_dir),
        _plot_reliability_relationship(daily, output_dir),
    ])
    if not outage_context.empty and outage_context["pre_24h_latest_voltage_v"].notna().any():
        written.append(_plot_outage_context(outage_context, output_dir))
    return summary, written


def load_reliability_exports(output_dir):
    """Consume audit CSV outputs when available; return (outages, daily)."""
    output_dir = Path(output_dir)
    outage_path = output_dir / "outage_intervals.csv"
    daily_path = output_dir / "daily_reliability.csv"
    if not outage_path.exists() or not daily_path.exists():
        return None, None

    outages = pd.read_csv(outage_path)
    for column in ("gap_start", "gap_end"):
        # The audit CSV intentionally serializes local wall-clock text without
        # an offset, matching the source export. Reuse its DST resolver rather
        # than treating those strings as UTC (which would shift every outage).
        naive = pd.Series(pd.to_datetime(outages[column]), index=outages.index)
        outages[column] = localize_timestamps(naive, [])
    daily = pd.read_csv(daily_path)
    daily["date"] = pd.to_datetime(daily["date"]).dt.date
    return outages, daily


def reliability_exports_match_data(df, daily):
    """Reject stale aggregate exports before joining them to another CSV."""
    required = {"date", "rows_received", "row_completeness", "largest_gap_minutes"}
    if daily is None or not required.issubset(daily.columns) or daily.empty:
        return False
    return (
        daily["date"].min() == df["timestamp"].dt.date.min()
        and daily["date"].max() == df["timestamp"].dt.date.max()
        and int(daily["rows_received"].sum()) == len(df)
    )


def _print_summary(summary, written, input_source):
    print("BATTERY ANALYTICS + ENERGY MODEL FOUNDATION")
    print(f"  Source                : {input_source}")
    print(f"  Status                : {summary['status']}")
    quality = summary["data_quality"]
    if summary["status"] == "skipped":
        print(f"  Reason                : {summary['reason']}")
    else:
        derived = summary["derived_summary"]
        print(
            f"  Valid battery period  : {quality['first_valid_timestamp']} -> "
            f"{quality['last_valid_timestamp']}"
        )
        print(f"  Valid readings        : {quality['valid_reading_count']}")
        print(f"  Completeness          : {100 * quality['completeness_since_commissioning']:.2f}% since commissioning")
        print(
            f"  Observed voltage      : {quality['observed_min_voltage_v']:.3f} -> "
            f"{quality['observed_max_voltage_v']:.3f} V"
        )
        print(
            "  Usable outage context : "
            f"{derived['outages_with_usable_pre_outage_battery_context']} / "
            f"{derived['significant_outages_analyzed']} significant outages"
        )
        relationship = derived["strongest_observed_reliability_relationship"]
        if relationship:
            print(
                f"  Strongest relationship: {relationship['x']} vs {relationship['y']}, "
                f"Spearman rho={relationship['spearman_rho']:.3f}, n={relationship['sample_size']}"
            )
    print("  Energy model          : uncalibrated; no hardware values assumed")
    print("\nOUTPUTS")
    for path in written:
        print(f"  {path}")


def build_parser():
    parser = argparse.ArgumentParser(
        description="Analyze battery-voltage behavior and its descriptive reliability context."
    )
    parser.add_argument("csv_path", help="Path to the shared HistoricalData CSV")
    parser.add_argument(
        "output_dir", nargs="?", default=str(PROJECT_DIR / "output"),
        help="Battery output directory (default: this project's output/)",
    )
    parser.add_argument(
        "--reliability-output-dir",
        default=str(DEFAULT_RELIABILITY_PROJECT / "audit_output"),
        help="Directory containing outage_intervals.csv and daily_reliability.csv",
    )
    parser.add_argument("--min-trend-samples", type=int, default=MIN_TREND_SAMPLES)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.min_trend_samples < 3:
        raise SystemExit("--min-trend-samples must be at least 3")
    validation_output = io.StringIO()
    with contextlib.redirect_stdout(validation_output):
        df, _ = load_and_validate_data(args.csv_path)
    outages, reliability_daily = load_reliability_exports(args.reliability_output_dir)
    source = "exported reliability-audit CSVs"
    if outages is None or not reliability_exports_match_data(df, reliability_daily):
        indexed = add_slot_index(df)
        outages = detect_outages(indexed, compute_gaps(indexed))
        reliability_daily = None
        source = "stable reliability helpers (exports unavailable)"
    summary, written = run_battery_analysis(
        df, args.output_dir, outages=outages,
        min_trend_samples=args.min_trend_samples,
        reliability_daily=reliability_daily,
    )
    summary["reliability_context_source"] = source
    # Rewrite the summary after adding provenance.
    summary_path = Path(args.output_dir) / "battery_summary.json"
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(_json_safe(summary), handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    _print_summary(summary, written, args.csv_path)


if __name__ == "__main__":
    main()
