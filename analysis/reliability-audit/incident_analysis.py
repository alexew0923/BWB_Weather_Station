"""Focused, evidence-bounded analysis of one telemetry incident."""

import pandas as pd

from audit_config import (
    DAY_OVER_BASELINE_TOLERANCE,
    GAP_NOMINAL_MINUTES,
    GAP_SUB_NOMINAL_MINUTES,
    PLAUSIBLE_SENSOR_RANGES,
    SENSOR_COLUMNS,
    STATION_TIMEZONE,
    active_minutes_between,
    scheduled_transmissions_between,
)
from outage_analysis import compute_gaps, detect_outages, real_outages
from reliability_metrics import add_slot_index, find_commissioning_date


def parse_incident_timestamp(text):
    """Parse a CLI timestamp, treating offset-free input as Halifax local time."""
    try:
        parsed = pd.Timestamp(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid timestamp {text!r}: {exc}") from exc

    if pd.isna(parsed):
        raise ValueError(f"Invalid timestamp {text!r}")

    if parsed.tzinfo is None:
        try:
            return parsed.tz_localize(
                STATION_TIMEZONE, ambiguous="raise", nonexistent="raise"
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Timestamp {text!r} is ambiguous or does not exist in Halifax "
                "local time; provide an explicit UTC offset"
            ) from exc
    return parsed.tz_convert(STATION_TIMEZONE)


def select_detected_outage(df, outage_number):
    """Return one 1-based real outage from the audit's existing outage table."""
    indexed = add_slot_index(df) if "slot" not in df.columns else df
    outages = real_outages(detect_outages(indexed, compute_gaps(indexed))).reset_index(drop=True)
    if outage_number < 1 or outage_number > len(outages):
        raise ValueError(
            f"Outage index must be between 1 and {len(outages)} "
            "(real outages in chronological order)"
        )
    row = outages.iloc[outage_number - 1]
    return row["gap_start"], row["gap_end"], row.to_dict(), len(outages)


def _hours(delta):
    return delta.total_seconds() / 3600.0


def _iso(moment):
    return moment.isoformat() if moment is not None and not pd.isna(moment) else None


def _duration_seconds(start, end):
    return (end.tz_convert("UTC") - start.tz_convert("UTC")).total_seconds()


def _window_rows(df, start, end, include_end=False):
    end_test = df["timestamp"] <= end if include_end else df["timestamp"] < end
    return df[(df["timestamp"] >= start) & end_test].copy()


def _gap_stats(rows):
    gaps = compute_gaps(rows).dropna()
    if gaps.empty:
        return {
            "typical_interarrival_minutes": None,
            "minor_gaps": 0,
            "significant_gaps": 0,
            "repeat_transmissions": 0,
            "irregular_gaps": 0,
        }
    outages = detect_outages(rows, gaps.reindex(rows.index))
    real = real_outages(outages)
    return {
        "typical_interarrival_minutes": round(float(gaps.median()), 3),
        "minor_gaps": int((real["severity"] == "minor").sum()),
        "significant_gaps": int(real["severity"].isin(["moderate", "major", "critical"]).sum()),
        "repeat_transmissions": int((gaps <= GAP_SUB_NOMINAL_MINUTES).sum()),
        "irregular_gaps": int((gaps > GAP_NOMINAL_MINUTES).sum()),
    }


def _battery_summary(rows, full_df, window_end):
    commissioned = find_commissioning_date(full_df, "Battery Voltage")
    low, high = PLAUSIBLE_SENSOR_RANGES["Battery Voltage"]
    plausible = rows["Battery Voltage"].notna() & rows["Battery Voltage"].between(low, high)
    valid = rows.loc[plausible, ["timestamp", "Battery Voltage"]]
    if valid.empty:
        if commissioned is None or commissioned > window_end.date():
            status = "not commissioned by this window"
        elif rows["Battery Voltage"].notna().any():
            status = "no plausible samples in this window"
        else:
            status = "commissioned, but unavailable in this window"
        return {
            "status": status,
            "samples": 0,
            "first_volts": None,
            "last_volts": None,
            "change_volts": None,
            "span_hours": None,
            "trend_volts_per_hour": None,
        }

    first = valid.iloc[0]
    last = valid.iloc[-1]
    first_v = float(first["Battery Voltage"]) / 1000.0
    last_v = float(last["Battery Voltage"]) / 1000.0
    span_hours = _duration_seconds(first["timestamp"], last["timestamp"]) / 3600.0
    enough = len(valid) >= 2 and span_hours > 0
    return {
        "status": "available" if enough else "insufficient samples for trend",
        "samples": len(valid),
        "first_volts": round(first_v, 4),
        "last_volts": round(last_v, 4),
        "change_volts": round(last_v - first_v, 4) if enough else None,
        "span_hours": round(span_hours, 3) if enough else None,
        "trend_volts_per_hour": round((last_v - first_v) / span_hours, 6) if enough else None,
    }


def _sensor_summary(rows, full_df, window_end):
    result = {}
    for column in SENSOR_COLUMNS:
        commissioned = find_commissioning_date(full_df, column)
        populated = int(rows[column].notna().sum())
        if commissioned is None or commissioned > window_end.date():
            status = "not commissioned by this window"
            completeness = None
        elif rows.empty:
            status = "no telemetry rows in window"
            completeness = None
        else:
            status = "available" if populated else "missing from all received rows"
            completeness = round(populated / len(rows), 4)
        low, high = PLAUSIBLE_SENSOR_RANGES[column]
        implausible = int(
            (rows[column].notna() & ((rows[column] < low) | (rows[column] > high))).sum()
        )
        result[column] = {
            "status": status,
            "populated": populated,
            "received_rows": len(rows),
            "completeness": completeness,
            "implausible_values": implausible,
        }
    return result


def _window_summary(rows, full_df, start, end, expected_override=None):
    expected = (
        scheduled_transmissions_between(start, end)
        if expected_override is None else expected_override
    )
    stats = _gap_stats(rows)
    stats.update({
        "start": _iso(start),
        "end": _iso(end),
        "expected_readings": expected,
        "received_readings": len(rows),
        "telemetry_completeness": round(min(1.0, len(rows) / expected), 4) if expected else None,
        "over_baseline": bool(expected and len(rows) / expected > DAY_OVER_BASELINE_TOLERANCE),
        "battery": _battery_summary(rows, full_df, end),
        "sensors": _sensor_summary(rows, full_df, end),
    })
    return stats


def _interpretation(incident, pre, post):
    observed = []
    suggestive = []
    if incident["received_readings"] == 0:
        observed.append("No telemetry rows reached the historical dataset during the incident interval.")
    else:
        observed.append(
            f"{incident['received_readings']} telemetry row(s) reached the historical dataset "
            f"during {incident['expected_readings']} scheduled opportunity/opportunities."
        )
    if incident["scheduled_inactive_hours"] > 0:
        observed.append(
            f"The interval included {incident['scheduled_inactive_hours']:.2f} h of scheduled inactive time."
        )
    if incident["repeat_transmissions"]:
        observed.append(
            f"{incident['repeat_transmissions']} sub-minute repeat transmission(s) appeared during the interval."
        )
    pre_battery = pre["battery"]
    if pre_battery["trend_volts_per_hour"] is not None and pre_battery["trend_volts_per_hour"] < 0:
        observed.append("Battery voltage declined during the selected pre-incident window.")
        suggestive.append("The battery decline may be relevant, but it does not establish causation.")
    missing_after = [
        sensor for sensor, values in post["sensors"].items()
        if values["status"] == "missing from all received rows"
    ]
    if post["received_readings"] and missing_after:
        observed.append("Telemetry resumed while these commissioned sensors remained missing: " + ", ".join(missing_after) + ".")
    if not suggestive:
        suggestive.append("No causal pattern is established by the selected telemetry windows.")
    not_determinable = [
        "The historical dataset cannot distinguish sensor, transmitter, power, ESP-NOW, receiver, I2C bridge, Wi-Fi, Apps Script, or Google Sheets failure domains.",
        "Component-level root cause is not determinable from delivery telemetry alone.",
    ]
    return {"observed": observed, "suggestive": suggestive, "not_determinable": not_determinable}


def analyze_incident(
    df, start, end, before_hours=12.0, after_hours=12.0, source=None,
    detected_outage=None,
):
    """Build a JSON-serialisable report for one half-open interval [start, end)."""
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("Incident timestamps must be timezone-aware")
    start = start.tz_convert(STATION_TIMEZONE)
    end = end.tz_convert(STATION_TIMEZONE)
    if end.tz_convert("UTC") <= start.tz_convert("UTC"):
        raise ValueError("Incident end must be after incident start")
    if before_hours < 0 or after_hours < 0:
        raise ValueError("Before/after hours must be non-negative")

    before = pd.Timedelta(hours=before_hours)
    after = pd.Timedelta(hours=after_hours)
    pre_start = (start.tz_convert("UTC") - before).tz_convert(STATION_TIMEZONE)
    post_end = (end.tz_convert("UTC") + after).tz_convert(STATION_TIMEZONE)

    if detected_outage is None:
        pre_rows = _window_rows(df, pre_start, start)
        incident_rows = _window_rows(df, start, end)
        incident_expected = None
        interval_semantics = "start inclusive, end exclusive"
    else:
        # Existing outages are gaps bounded by the last received row and the
        # recovery row. Neither boundary is evidence of telemetry *during* the
        # loss, and the outage table already carries the authoritative count of
        # scheduled slots missed between them.
        pre_rows = _window_rows(df, pre_start, start, include_end=True)
        incident_rows = df[(df["timestamp"] > start) & (df["timestamp"] < end)].copy()
        incident_expected = int(detected_outage["missed_transmissions"])
        interval_semantics = "detected gap; received boundary rows excluded"
    post_rows = _window_rows(df, end, post_end, include_end=True)
    pre = _window_summary(pre_rows, df, pre_start, start)
    incident_window = _window_summary(
        incident_rows, df, start, end, expected_override=incident_expected
    )
    post = _window_summary(post_rows, df, end, post_end)

    has_valid_sensor = df[SENSOR_COLUMNS].notna().any(axis=1)
    prior_test = df["timestamp"] <= start if detected_outage is not None else df["timestamp"] < start
    prior = df.loc[prior_test & has_valid_sensor, "timestamp"]
    later = df.loc[(df["timestamp"] >= end) & has_valid_sensor, "timestamp"]
    last_before = prior.iloc[-1] if not prior.empty else None
    first_after = later.iloc[0] if not later.empty and later.iloc[0] <= post_end else None
    elapsed_hours = _duration_seconds(start, end) / 3600.0
    active_hours = active_minutes_between(start.tz_convert("UTC"), end.tz_convert("UTC")) / 60.0

    incident = {
        "start": _iso(start),
        "end": _iso(end),
        "interval_semantics": interval_semantics,
        "duration_hours": round(elapsed_hours, 4),
        "schedulable_hours": round(active_hours, 4),
        "scheduled_inactive_hours": round(max(0.0, elapsed_hours - active_hours), 4),
        "overlaps_scheduled_inactive_period": elapsed_hours - active_hours > 1e-9,
        **{key: incident_window[key] for key in (
            "expected_readings", "received_readings", "telemetry_completeness",
            "over_baseline", "repeat_transmissions", "irregular_gaps", "battery", "sensors"
        )},
    }
    pre["last_reading_before_incident"] = _iso(last_before)
    post["first_reading_after_incident"] = _iso(first_after)
    post["recovery_delay_hours"] = (
        round(_duration_seconds(end, first_after) / 3600.0, 4) if first_after is not None else None
    )
    post["recovery_within_selected_window"] = first_after is not None

    result = {
        "source": source,
        "analysis_timezone": STATION_TIMEZONE.key,
        "incident": incident,
        "pre_window": pre,
        "post_window": post,
    }
    result["interpretation"] = _interpretation(incident, pre, post)
    return result
