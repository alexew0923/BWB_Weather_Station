"""CLI and deterministic calculations for historical sensor-health analysis."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent
RELIABILITY_PROJECT = PROJECT_DIR.parent / "reliability-audit"
if str(RELIABILITY_PROJECT) not in sys.path:
    # Keep this project's own reporting.py/visualization.py ahead of sibling
    # modules with the same names while still reusing the audit's stable helpers.
    sys.path.append(str(RELIABILITY_PROJECT))

from data_validation import load_and_validate_data, localize_timestamps  # noqa: E402

from sensor_rules import (  # noqa: E402
    ALL_SENSORS,
    HISTORICAL_REGIMES,
    MIN_RATE_INTERVAL_MINUTES,
    MISSING_RUN_CONTINUITY_MINUTES,
    PRIMARY_SENSORS,
    SENSOR_RULES,
    STATISTICAL_RATE_QUANTILE,
)


EVENT_COLUMNS = [
    "sensor", "event_type", "start_time", "end_time", "duration_minutes",
    "sample_count", "severity", "observed_value", "context",
]


def elapsed_minutes(start, end):
    """Real elapsed minutes between timezone-aware instants, including DST."""
    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("elapsed-time calculations require timezone-aware timestamps")
    return (end.tz_convert("UTC") - start.tz_convert("UTC")).total_seconds() / 60.0


def prepare_sensor_frame(df):
    """Validate a synthetic/loaded frame and coerce malformed sensor cells."""
    required = ["timestamp", *ALL_SENSORS]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError("missing required column(s): " + ", ".join(missing))

    prepared = df.copy()
    parsed = pd.to_datetime(prepared["timestamp"], errors="coerce", format="mixed")
    malformed_timestamps = int(parsed.isna().sum())
    prepared = prepared.loc[parsed.notna()].copy()
    parsed = parsed.loc[parsed.notna()]
    if prepared.empty:
        raise ValueError("no usable rows")

    if parsed.dt.tz is None:
        parsed = localize_timestamps(parsed.reset_index(drop=True), [])
        prepared = prepared.reset_index(drop=True)
    prepared["timestamp"] = parsed.to_numpy()

    malformed_numeric = {}
    for column in ALL_SENSORS + ("Count",):
        if column not in prepared.columns:
            if column == "Count":
                prepared[column] = np.nan
                continue
            raise ValueError(f"missing required column: {column}")
        original_non_null = prepared[column].notna()
        prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
        malformed_numeric[column] = int(
            (original_non_null & prepared[column].isna()).sum()
        )

    prepared = prepared.sort_values("timestamp").reset_index(drop=True)
    return prepared, {
        "malformed_timestamps_dropped": malformed_timestamps,
        "malformed_numeric_cells": malformed_numeric,
    }


def load_sensor_data(path):
    """Reuse the reliability audit's validated, DST-safe historical loader."""
    df, validation = load_and_validate_data(path)
    prepared, coercion = prepare_sensor_frame(df)
    validation.update(coercion)
    return prepared, validation


def soil_expected_opportunity_mask(df):
    """Best-supported soil sampling opportunities, with explicit uncertainty.

    Older committed transmitter code sampled soil every sixth boot and emitted
    zero otherwise; ingestion later blanked those zeros. Current code samples
    every transmission. Git history cannot prove deployment dates, so this mask
    is an interpretation aid rather than a claim about physical failures.
    """
    day = df["timestamp"].dt.date
    every_transmission_era = day >= pd.Timestamp("2026-06-19").date()
    periodic = (
        df["Count"].notna()
        & np.isclose(np.mod(df["Count"], 6), 0)
        & df["Temperature"].notna()
        & (df["Temperature"] > 0)
    )
    return pd.Series(every_transmission_era, index=df.index) | periodic


def sensor_expected_mask(df, sensor):
    if sensor == "Soil Moisture":
        return soil_expected_opportunity_mask(df)
    if sensor == "Battery Voltage":
        populated = df[sensor].notna()
        if not populated.any():
            return pd.Series(False, index=df.index)
        return df["timestamp"] >= df.loc[populated, "timestamp"].min()
    return pd.Series(True, index=df.index)


def _groups_from_mask(df, mask, continuity_minutes=MISSING_RUN_CONTINUITY_MINUTES):
    """Group true rows without bridging absent telemetry or schedule gaps."""
    positions = list(df.index[pd.Series(mask, index=df.index).fillna(False)])
    if not positions:
        return []
    groups = [[positions[0]]]
    for position in positions[1:]:
        previous = groups[-1][-1]
        continuous = (
            position == previous + 1
            and elapsed_minutes(
                df.loc[previous, "timestamp"], df.loc[position, "timestamp"]
            ) <= continuity_minutes
        )
        if continuous:
            groups[-1].append(position)
        else:
            groups.append([position])
    return groups


def _event(sensor, event_type, frame, severity, observed_value, context):
    start = frame["timestamp"].iloc[0]
    end = frame["timestamp"].iloc[-1]
    return {
        "sensor": sensor,
        "event_type": event_type,
        "start_time": start,
        "end_time": end,
        "duration_minutes": round(elapsed_minutes(start, end), 3),
        "sample_count": len(frame),
        "severity": severity,
        "observed_value": observed_value,
        "context": context,
    }


def compute_daily_sensor_metrics(df):
    """Daily field completeness, always within received telemetry rows."""
    records = []
    for day, rows in df.groupby(df["timestamp"].dt.date, sort=True):
        record = {"date": day, "rows_received": len(rows)}
        for sensor in ALL_SENSORS:
            expected = sensor_expected_mask(rows, sensor)
            populated = rows[sensor].notna()
            record[f"{sensor} populated"] = int(populated.sum())
            record[f"{sensor} completeness"] = float(populated.mean())
            denominator = int(expected.sum())
            record[f"{sensor} expected opportunities"] = denominator
            record[f"{sensor} interpreted completeness"] = (
                float((populated & expected).sum() / denominator)
                if denominator else math.nan
            )
        records.append(record)
    return pd.DataFrame(records)


def compute_regime_metrics(df):
    """Completeness and distributions across documented interpretation regimes."""
    records = []
    for index, regime in enumerate(HISTORICAL_REGIMES):
        start = pd.Timestamp(regime.starts_on) if regime.starts_on != pd.Timestamp.min.date() else None
        next_start = (
            pd.Timestamp(HISTORICAL_REGIMES[index + 1].starts_on)
            if index + 1 < len(HISTORICAL_REGIMES) else None
        )
        mask = pd.Series(True, index=df.index)
        local_days = pd.to_datetime(pd.Series(df["timestamp"].dt.date, index=df.index))
        if start is not None:
            mask &= local_days >= start
        if next_start is not None:
            mask &= local_days < next_start
        rows = df.loc[mask]
        if rows.empty:
            continue
        for sensor in ALL_SENSORS:
            expected = sensor_expected_mask(rows, sensor)
            values = rows.loc[expected & rows[sensor].notna(), sensor]
            denominator = int(expected.sum())
            records.append({
                "regime": regime.label,
                "regime_start": rows["timestamp"].min(),
                "regime_end": rows["timestamp"].max(),
                "sensor": sensor,
                "received_rows": len(rows),
                "expected_sensor_opportunities": denominator,
                "populated_expected_opportunities": int(values.size),
                "completeness": float(values.size / denominator) if denominator else math.nan,
                "median": float(values.median()) if not values.empty else math.nan,
                "iqr": float(values.quantile(.75) - values.quantile(.25)) if not values.empty else math.nan,
                "std": float(values.std()) if len(values) > 1 else math.nan,
                "minimum": float(values.min()) if not values.empty else math.nan,
                "maximum": float(values.max()) if not values.empty else math.nan,
                "interpretation_note": regime.evidence_note,
            })
    return pd.DataFrame(records)


def detect_missing_runs(df):
    events = []
    for sensor in PRIMARY_SENSORS:
        expected = sensor_expected_mask(df, sensor)
        mask = expected & df[sensor].isna()
        for positions in _groups_from_mask(df, mask):
            rows = df.loc[positions]
            duration = elapsed_minutes(rows["timestamp"].iloc[0], rows["timestamp"].iloc[-1])
            severity = "critical" if duration >= 1440 else ("significant" if len(rows) > 1 else "minor")
            other = [name for name in ALL_SENSORS if name != sensor]
            present_counts = {name: int(rows[name].notna().sum()) for name in other}
            semantics = (
                "Only documented/estimated soil sampling opportunities are counted; deployment timing remains unverified. "
                if sensor == "Soil Moisture" else ""
            )
            events.append(_event(
                sensor, "missing_run", rows, severity, "missing",
                semantics + "Other fields present in received rows: "
                + ", ".join(f"{name}={count}/{len(rows)}" for name, count in present_counts.items()),
            ))
    return events


def detect_value_bound_events(df):
    events = []
    for sensor in PRIMARY_SENSORS:
        rule = SENSOR_RULES[sensor]
        values = df[sensor]
        impossible = values.notna() & (
            (values < rule.impossible_low) | (values > rule.impossible_high)
        )
        for positions in _groups_from_mask(df, impossible, rule.continuity_minutes):
            rows = df.loc[positions]
            observed = rows[sensor]
            events.append(_event(
                sensor, "impossible_reading", rows, "critical",
                f"{observed.min():g}..{observed.max():g} {rule.unit}",
                f"Outside documented broad bounds [{rule.impossible_low:g}, {rule.impossible_high:g}] {rule.unit}; values are reported, not deleted.",
            ))

        if rule.suspicious_low is None and rule.suspicious_high is None:
            continue
        suspicious = values.notna() & ~impossible
        if rule.suspicious_low is not None:
            suspicious &= values < rule.suspicious_low
        if rule.suspicious_high is not None:
            suspicious |= values.notna() & ~impossible & (values > rule.suspicious_high)
        for positions in _groups_from_mask(df, suspicious, rule.continuity_minutes):
            rows = df.loc[positions]
            observed = rows[sensor]
            events.append(_event(
                sensor, "suspicious_reading", rows, "significant",
                f"{observed.min():g}..{observed.max():g} {rule.unit}",
                "Broadly possible but outside the conservative local plausibility band; no reading is removed.",
            ))
    return events


def _flatline_runs(df, sensor):
    rule = SENSOR_RULES[sensor]
    expected = sensor_expected_mask(df, sensor)
    valid = df.loc[expected & df[sensor].notna(), ["timestamp", sensor]]
    if valid.empty:
        return []
    positions = list(valid.index)
    runs = []
    current = [positions[0]]
    low = high = float(df.loc[positions[0], sensor])
    for position in positions[1:]:
        previous = current[-1]
        value = float(df.loc[position, sensor])
        gap = elapsed_minutes(df.loc[previous, "timestamp"], df.loc[position, "timestamp"])
        new_low, new_high = min(low, value), max(high, value)
        adjacent_opportunity = sensor == "Soil Moisture" or position == previous + 1
        if adjacent_opportunity and gap <= rule.continuity_minutes and new_high - new_low <= rule.flatline_tolerance:
            current.append(position)
            low, high = new_low, new_high
        else:
            runs.append(current)
            current = [position]
            low = high = value
    runs.append(current)
    return runs


def detect_flatlines(df):
    events = []
    for sensor in PRIMARY_SENSORS:
        rule = SENSOR_RULES[sensor]
        for positions in _flatline_runs(df, sensor):
            rows = df.loc[positions]
            duration = elapsed_minutes(rows["timestamp"].iloc[0], rows["timestamp"].iloc[-1])
            if len(rows) < rule.flatline_min_samples or duration < rule.flatline_min_minutes:
                continue
            values = rows[sensor]
            severity = "critical" if duration >= 1440 else "significant"
            context = (
                f"Range {values.max() - values.min():g} {rule.unit} is within the configured {rule.flatline_tolerance:g} tolerance."
            )
            if sensor == "Rain Value" and np.isclose(values.median(), 4095):
                severity = "minor"
                context += " Full-scale dry/wetness saturation can be a normal dry period and is not classified as sensor failure."
            if sensor == "Humidity" and np.isclose(values.median(), 100, atol=rule.flatline_tolerance):
                severity = "minor"
                context += " Saturation at 100% RH can be environmental or sensor saturation and is not classified as failure by itself."
            events.append(_event(
                sensor, "flatline", rows, severity,
                f"{values.min():g}..{values.max():g} {rule.unit}", context,
            ))
    return events


def compute_rate_table(df):
    """One row per valid adjacent change, using actual elapsed UTC minutes."""
    records = []
    for sensor in ALL_SENSORS:
        rule = SENSOR_RULES[sensor]
        rows = df.loc[df[sensor].notna(), ["timestamp", sensor]].copy()
        if len(rows) < 2:
            continue
        rows["previous_time"] = rows["timestamp"].shift()
        rows["previous_value"] = rows[sensor].shift()
        rows["elapsed_minutes"] = [
            math.nan if pd.isna(start) else elapsed_minutes(start, end)
            for start, end in zip(rows["previous_time"], rows["timestamp"])
        ]
        rows["delta"] = rows[sensor] - rows["previous_value"]
        rows["rate_per_minute"] = rows["delta"] / rows["elapsed_minutes"]
        eligible = rows[
            (rows["elapsed_minutes"] >= MIN_RATE_INTERVAL_MINUTES)
            & (rows["elapsed_minutes"] <= rule.continuity_minutes)
        ].copy()
        eligible["sensor"] = sensor
        records.append(eligible[[
            "sensor", "previous_time", "timestamp", "previous_value", sensor,
            "elapsed_minutes", "delta", "rate_per_minute",
        ]].rename(columns={sensor: "value"}))
    if not records:
        return pd.DataFrame(columns=[
            "sensor", "previous_time", "timestamp", "previous_value", "value",
            "elapsed_minutes", "delta", "rate_per_minute",
        ])
    return pd.concat(records, ignore_index=True)


def detect_rate_events(rate_table):
    events = []
    thresholds = {}
    for sensor in PRIMARY_SENSORS:
        rows = rate_table[rate_table["sensor"] == sensor]
        if rows.empty:
            thresholds[sensor] = math.nan
            continue
        threshold = float(rows["rate_per_minute"].abs().quantile(STATISTICAL_RATE_QUANTILE))
        thresholds[sensor] = threshold
        rule = SENSOR_RULES[sensor]
        statistical = rows["rate_per_minute"].abs() > threshold
        physical = (
            rows["rate_per_minute"].abs() > rule.physical_rate_limit
            if rule.physical_rate_limit is not None else pd.Series(False, index=rows.index)
        )
        for _, change in rows.loc[statistical | physical].iterrows():
            is_physical = bool(physical.loc[change.name])
            event_type = "physically_implausible_change" if is_physical else "statistical_rate_outlier"
            severity = "critical" if is_physical else "significant"
            frame = pd.DataFrame({"timestamp": [change["previous_time"], change["timestamp"]]})
            events.append(_event(
                sensor, event_type, frame, severity,
                f"{change['previous_value']:g} -> {change['value']:g} {rule.unit}",
                f"Delta {change['delta']:g} over {change['elapsed_minutes']:.3f} real minutes; rate {change['rate_per_minute']:.6g} {rule.unit}/min; empirical q{STATISTICAL_RATE_QUANTILE:g} absolute-rate threshold={threshold:.6g}.",
            ))
    return events, thresholds


def detect_cross_sensor_events(df):
    """Describe co-missing signatures on rows that were actually received."""
    adjusted_missing = pd.DataFrame(index=df.index)
    for sensor in PRIMARY_SENSORS:
        adjusted_missing[sensor] = df[sensor].isna() & sensor_expected_mask(df, sensor)
    signatures = adjusted_missing.apply(
        lambda row: tuple(sensor for sensor in PRIMARY_SENSORS if bool(row[sensor])), axis=1
    )
    events = []
    for signature in sorted(set(signatures), key=lambda value: (len(value), value)):
        if len(signature) < 2:
            continue
        groups = _groups_from_mask(df, signatures == signature)
        for positions in groups:
            rows = df.loc[positions]
            if set(signature) == {"Temperature", "Humidity"}:
                category = "sensor-module-level pattern"
            elif len(signature) == len(PRIMARY_SENSORS):
                category = "telemetry-wide anomaly"
            else:
                category = "multi-sensor anomaly"
            severity = "critical" if category == "telemetry-wide anomaly" else "significant"
            events.append(_event(
                "+".join(signature), "cross_sensor_missing_signature", rows,
                severity, "+".join(signature),
                f"{category}; a telemetry row was received, so this is not a missing-row outage. Shared cause is not determinable.",
            ))
    return events


def build_pressure_clusters(df):
    """Pressure-specific evidence with observed/suggestive/unknown language."""
    rule = SENSOR_RULES["Air Pressure"]
    values = df["Air Pressure"]
    masks = {
        "missing": sensor_expected_mask(df, "Air Pressure") & values.isna(),
        "impossible": values.notna() & ((values < rule.impossible_low) | (values > rule.impossible_high)),
    }
    records = []
    plausible = values.notna() & (values >= rule.impossible_low) & (values <= rule.impossible_high)
    for cluster_type, mask in masks.items():
        if cluster_type == "impossible":
            anomaly_positions = list(df.index[mask])
            grouped_positions = []
            for position in anomaly_positions:
                if (
                    grouped_positions
                    and elapsed_minutes(
                        df.loc[grouped_positions[-1][-1], "timestamp"],
                        df.loc[position, "timestamp"],
                    ) <= 60.0
                ):
                    grouped_positions[-1].append(position)
                else:
                    grouped_positions.append([position])
        else:
            grouped_positions = _groups_from_mask(df, mask, rule.continuity_minutes)
        for positions in grouped_positions:
            rows = df.loc[positions]
            end_position = positions[-1]
            recovery = df.loc[(df.index > end_position) & plausible, ["timestamp", "Air Pressure"]].head(1)
            previous = df.loc[(df.index < positions[0]) & plausible, ["timestamp", "Air Pressure"]].tail(1)
            observed_values = rows["Air Pressure"].dropna()
            if cluster_type == "missing":
                observed = f"{len(rows)} received row(s) had pressure missing while other telemetry fields could remain populated."
                suggestive = "The anomaly is pressure-field-specific within received telemetry."
            else:
                counts = observed_values.value_counts()
                repeated_404 = int(np.isclose(observed_values, 4.04, atol=.005).sum())
                observed = (
                    f"{len(rows)} pressure value(s) outside [{rule.impossible_low:g}, {rule.impossible_high:g}] hPa; "
                    f"range {observed_values.min():g}..{observed_values.max():g} hPa; approximately 4.04 hPa occurred {repeated_404} time(s)."
                )
                suggestive = (
                    "Repeated approximately 4.04 hPa values indicate stable invalid pressure telemetry rather than plausible weather variation."
                    if repeated_404 else
                    "The values are incompatible with plausible local surface pressure and indicate invalid pressure telemetry."
                )
            records.append({
                "cluster_type": cluster_type,
                "start_time": rows["timestamp"].iloc[0],
                "end_time": rows["timestamp"].iloc[-1],
                "duration_minutes": round(elapsed_minutes(rows["timestamp"].iloc[0], rows["timestamp"].iloc[-1]), 3),
                "sample_count": len(rows),
                "observed": observed,
                "suggestive": suggestive,
                "not_determinable": "Historical telemetry cannot distinguish sensor fault, I2C communication, parsing/serialization, firmware, electrical instability, or ingestion behavior.",
                "previous_valid_time": None if previous.empty else previous["timestamp"].iloc[0],
                "previous_valid_hpa": math.nan if previous.empty else float(previous["Air Pressure"].iloc[0]),
                "recovery_time": None if recovery.empty else recovery["timestamp"].iloc[0],
                "recovery_hpa": math.nan if recovery.empty else float(recovery["Air Pressure"].iloc[0]),
            })
    return pd.DataFrame(records)


def build_sensor_summary(df, events):
    records = []
    for sensor in ALL_SENSORS:
        expected = sensor_expected_mask(df, sensor)
        denominator = int(expected.sum())
        populated = int((expected & df[sensor].notna()).sum())
        relevant = events[events["sensor"] == sensor]
        counts = relevant["event_type"].value_counts()
        missing = relevant[relevant["event_type"] == "missing_run"]
        records.append({
            "sensor": sensor,
            "received_rows": len(df),
            "expected_sensor_opportunities": denominator,
            "populated_expected_opportunities": populated,
            "completeness": float(populated / denominator) if denominator else math.nan,
            "raw_field_completeness": float(df[sensor].notna().mean()),
            "missing_runs": int(counts.get("missing_run", 0)),
            "longest_missing_run_minutes": float(missing["duration_minutes"].max()) if not missing.empty else 0.0,
            "impossible_events": int(counts.get("impossible_reading", 0)),
            "suspicious_events": int(counts.get("suspicious_reading", 0)),
            "flatline_events": int(counts.get("flatline", 0)),
            "large_change_events": int(counts.get("statistical_rate_outlier", 0) + counts.get("physically_implausible_change", 0)),
        })
    return pd.DataFrame(records)


def analyze_sensor_health(df):
    """Run the complete in-memory analysis and return its tables."""
    df, coercion = prepare_sensor_frame(df)
    daily = compute_daily_sensor_metrics(df)
    regimes = compute_regime_metrics(df)
    rate_table = compute_rate_table(df)
    event_records = []
    event_records.extend(detect_missing_runs(df))
    event_records.extend(detect_value_bound_events(df))
    event_records.extend(detect_flatlines(df))
    rate_events, rate_thresholds = detect_rate_events(rate_table)
    event_records.extend(rate_events)
    event_records.extend(detect_cross_sensor_events(df))
    events = pd.DataFrame(event_records, columns=EVENT_COLUMNS)
    if not events.empty:
        events = events.sort_values(["start_time", "sensor", "event_type"]).reset_index(drop=True)
    pressure = build_pressure_clusters(df)
    summary = build_sensor_summary(df, events)
    return {
        "data": df,
        "coercion": coercion,
        "daily": daily,
        "regimes": regimes,
        "rates": rate_table,
        "rate_thresholds": rate_thresholds,
        "events": events,
        "pressure_clusters": pressure,
        "sensor_summary": summary,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Analyze historical sensor measurement health within received telemetry rows."
    )
    parser.add_argument("csv_path", help="Path to HistoricalData.csv")
    parser.add_argument("output_dir", nargs="?", default=str(PROJECT_DIR / "output"))
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    df, validation = load_sensor_data(args.csv_path)
    results = analyze_sensor_health(df)
    results["validation"] = validation

    from reporting import write_outputs
    from visualization import create_plots

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    write_outputs(results, output_dir, args.csv_path)
    create_plots(results, output_dir)
    from reporting import print_console_summary
    print_console_summary(results, output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
