"""Daily row, sensor-completeness, and reliability calculations."""

from datetime import timedelta

import numpy as np
import pandas as pd

from audit_config import (
    CORE_SENSOR_COLUMNS,
    DAY_CORE_SENSOR_COMPLETENESS,
    DAY_GOOD_COMPLETENESS,
    DAY_OVER_BASELINE_TOLERANCE,
    DAY_PARTIAL_COMPLETENESS,
    DAY_SEVERE_COMPLETENESS,
    EXPECTED_TRANSMISSIONS_24H,
    EXPECTED_TRANSMISSIONS_POWERED,
    NIGHT_HOURS,
    SENSOR_COLUMNS,
    BASELINE_CHANGEOVER_DATE,
    NOMINAL_CYCLE_MINUTES,
    STATION_TIMEZONE,
    active_window_for,
    expected_transmissions_for,
)
from outage_analysis import real_outages


def add_slot_index(df):
    """
    Number every row by its position on the POWERED timeline.

    The slot index counts scheduled 5-minute transmission opportunities since
    the start of the dataset, skipping every minute the station was not powered.
    Two rows that straddle the nightly shutdown differ by only the handful of
    slots that were actually schedulable between them.

    Numbering is 1-based: a row arriving exactly as the first powered window
    opens occupies slot 1, so the number of slots up to and including any row is
    its own index. That is what makes the head/tail terms in
    reconcile_transmissions come out at zero for a complete day.

    Everything expected/missed downstream is a difference of two slot indices,
    which is what makes the reconciliation exact: the per-interval terms
    telescope instead of each carrying its own rounding error.
    """
    first_day = df["timestamp"].iloc[0].date()
    last_day = df["timestamp"].iloc[-1].date()

    # Active minutes elapsed at the moment each day's powered window opens.
    minutes_at_open = {}
    running = 0.0
    day = first_day
    while day <= last_day:
        open_at, close_at = active_window_for(day)
        minutes_at_open[day] = running
        running += (close_at - open_at).total_seconds() / 60.0
        day += timedelta(days=1)

    def elapsed(moment):
        local_day = moment.date()
        open_at, close_at = active_window_for(local_day)
        inside = (min(moment, close_at) - open_at).total_seconds() / 60.0
        return minutes_at_open[local_day] + max(0.0, inside)

    active = df["timestamp"].map(elapsed).to_numpy()
    df = df.copy()
    df["slot"] = np.rint(active / NOMINAL_CYCLE_MINUTES).astype(int) + 1
    return df


def reconcile_transmissions(df, daily):
    """
    Account for every scheduled transmission in the span.

    Returns the terms of the identity

        expected == received + missed - surplus

    where `missed` sums the empty slots before the first row, between
    consecutive rows, and after the last row, and `surplus` counts rows that
    occupied no new slot at all (duplicates and sub-minute repeat
    transmissions). The residual is reported rather than absorbed: a non-zero
    value means the accounting is wrong and should be investigated, not
    explained away.
    """
    slots = df["slot"].to_numpy()
    span_close_slot = int(daily["expected_rows"].sum())

    # Unclamped per-interval losses: head, each inter-row gap, then the tail.
    head = slots[0] - 1
    between = np.diff(slots) - 1
    tail = span_close_slot - slots[-1]

    terms = np.concatenate(([head], between, [tail]))
    missed = int(terms[terms > 0].sum())
    surplus = int(-terms[terms < 0].sum())

    return {
        "expected": span_close_slot,
        "received": len(df),
        "missed": missed,
        "surplus": surplus,
        "residual": span_close_slot - (len(df) + missed - surplus),
    }


def compute_daily_row_completeness(df):
    """
    Rows received per calendar day against that day's baseline (288 or 204).

    Every calendar day in the span is present, including days with zero rows --
    a day with no data is the most severe reliability event there is, and
    grouping alone would drop it entirely.

    Two completeness figures are produced on purpose:
      row_completeness        capped at 1.0, for "how much of the day did we get"
      row_completeness_raw    uncapped, so days that exceed the physical maximum
                              stay visible instead of being flattened to 100%
    """
    per_day = df.groupby(df["timestamp"].dt.date).size()

    # Every calendar day in the span, as plain dates so the index matches the
    # groupby keys above.
    full_span = [
        stamp.date() for stamp in pd.date_range(
            df["timestamp"].min().date(), df["timestamp"].max().date(), freq="D"
        )
    ]
    per_day = per_day.reindex(full_span, fill_value=0)

    daily = pd.DataFrame(
        {"date": per_day.index, "rows_received": per_day.to_numpy()}
    )
    # Per-day denominator: 288 before the changeover, 204 after.
    daily["expected_rows"] = daily["date"].map(expected_transmissions_for)
    daily["row_completeness_raw"] = daily["rows_received"] / daily["expected_rows"]
    daily["row_completeness"] = daily["row_completeness_raw"].clip(upper=1.0)
    return daily


def add_daily_gap_stats(daily, outages):
    """Attach the largest real outage starting on each day."""
    if outages.empty:
        daily["largest_gap_minutes"] = 0.0
        return daily

    real = real_outages(outages)
    if real.empty:
        daily["largest_gap_minutes"] = 0.0
        return daily

    largest = real.groupby(real["gap_start"].dt.date)["gap_minutes"].max()
    daily["largest_gap_minutes"] = (
        daily["date"].map(largest).fillna(0.0)
    )
    return daily


def find_commissioning_date(df, column):
    """
    First date on which a sensor ever reported a value.

    Battery Voltage is 100% null from November to March and 0% null from April
    onwards. That is a feature being switched on mid-deployment, not a sensor
    failing. Measuring it across the full span would report ~36% "missing" for a
    sensor that has never actually failed, so completeness is also reported from
    the commissioning date onwards.
    """
    populated = df.loc[df[column].notna(), "timestamp"]
    if populated.empty:
        return None
    return populated.min().date()


def compute_sensor_completeness(df):
    """
    Daily field-population rate per sensor, measured ONLY against rows that were
    actually received that day.

    This is the strict separation the audit depends on: the denominator is
    rows_received, never the daily transmission baseline. A day where the
    station sent 10 rows and all 10 had a temperature is 100% temperature
    completeness and 5% row completeness -- two different failures, reported
    separately.
    """
    by_date = df.groupby(df["timestamp"].dt.date)

    sensor_daily = pd.DataFrame({"rows_received": by_date.size()})
    for column in SENSOR_COLUMNS:
        populated = by_date[column].apply(lambda s: int(s.notna().sum()))
        sensor_daily[f"{column} populated"] = populated
        sensor_daily[f"{column} completeness"] = populated / sensor_daily["rows_received"]

    return sensor_daily.reset_index(names="date")


def classify_day(row):
    """
    Assign one failure class per day.

    Structure follows deprecatedReliabilityAudit.js so the two are
    comparable, with the per-day denominator correction and the other changes
    documented at the threshold constants. Order matters: the row-level classes
    are tested first, because a transmission failure explains missing sensor
    data but not the reverse.
    """
    rows = row["rows_received"]
    completeness = row["row_completeness"]

    if rows == 0:
        return "Full outage"

    # Meaningfully more rows than the schedule allows means the node was
    # re-transmitting far faster than its 5 min cycle -- a fault, not a good
    # day. The old in-sheet audit capped this at 100% and reported it as clean.
    # The tolerance keeps midnight-boundary jitter out of this class.
    if row["row_completeness_raw"] > DAY_OVER_BASELINE_TOLERANCE:
        return "Over-baseline (fast-cycling / repeat transmissions)"

    if completeness < DAY_SEVERE_COMPLETENESS:
        return "Severe transmission loss"
    if completeness < DAY_PARTIAL_COMPLETENESS:
        return "Partial transmission loss"

    # Row delivery was healthy, so any remaining problem is sensor-side.
    worst_core = min(
        row[f"{column} completeness"] for column in CORE_SENSOR_COLUMNS
    )
    if worst_core < DAY_CORE_SENSOR_COMPLETENESS:
        return "Sensor-level issue"

    if completeness >= DAY_GOOD_COMPLETENESS:
        return "Good day"
    return "Minor transmission loss"


def build_daily_reliability(daily, sensor_daily):
    """
    Join row-level and sensor-level daily tables and classify each day.

    Days with zero rows have no sensor columns to join, so their core-sensor
    completeness is filled with 0 -- they are already caught by 'Full outage'
    before the sensor test is reached.
    """
    merged = daily.merge(sensor_daily, on="date", how="left", suffixes=("", "_sensor"))
    merged["rows_received_sensor"] = merged["rows_received_sensor"].fillna(0)

    for column in SENSOR_COLUMNS:
        merged[f"{column} completeness"] = merged[f"{column} completeness"].fillna(0.0)
        merged[f"{column} populated"] = merged[f"{column} populated"].fillna(0).astype(int)

    merged["failure_class"] = merged.apply(classify_day, axis=1)
    return merged.drop(columns=["rows_received_sensor"])


def verify_baseline_regimes(df):
    """
    Re-derive the two-regime baseline from the data on every run.

    The denominator is the single most consequential constant in this audit, and
    it changes partway through the deployment. Rather than trusting
    BASELINE_CHANGEOVER_DATE, measure night-hour traffic either side of it: the
    station ran 24 h before, so night and day hours should carry comparable
    traffic; afterwards night traffic should be essentially zero.

    Returns one row per regime for printing, so a future schedule change shows up
    as a contradiction in the summary instead of quietly skewing the numbers.
    """
    day = df["timestamp"].dt.date
    is_night = df["timestamp"].dt.hour.isin(NIGHT_HOURS)

    regimes = []
    for label, mask, expected in [
        (f"before {BASELINE_CHANGEOVER_DATE}", day < BASELINE_CHANGEOVER_DATE,
         EXPECTED_TRANSMISSIONS_24H),
        (f"from {BASELINE_CHANGEOVER_DATE}", day >= BASELINE_CHANGEOVER_DATE,
         EXPECTED_TRANSMISSIONS_POWERED),
    ]:
        rows = df[mask]
        if rows.empty:
            continue

        night_rows = int(is_night[mask].sum())
        # Per hour-of-day slot PER DAY, so the two regimes are directly
        # comparable despite covering 7 vs 17 hours and 171 vs 101 days.
        # Dividing by hours alone (as this did) made the shorter, later regime
        # look busier per hour than the 24-hour one purely because it spans
        # fewer days.
        span_days = max(1, rows["timestamp"].dt.date.nunique())
        night_rate = night_rows / len(NIGHT_HOURS) / span_days
        day_rate = (len(rows) - night_rows) / (24 - len(NIGHT_HOURS)) / span_days

        regimes.append({
            "label": label,
            "expected": expected,
            "night_rate": night_rate,
            "day_rate": day_rate,
            "ratio": night_rate / day_rate if day_rate else float("nan"),
            "peak_day": int(rows.groupby(day[mask]).size().max()),
        })
    return regimes
