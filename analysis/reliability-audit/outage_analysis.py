"""Inter-arrival gap calculation and outage classification."""

import pandas as pd

from audit_config import (
    GAP_MAJOR_MINUTES,
    GAP_MINOR_MINUTES,
    GAP_MODERATE_MINUTES,
    GAP_NOMINAL_MINUTES,
    GAP_SUB_NOMINAL_MINUTES,
    NIGHTLY_SHUTDOWN_END_HOUR,
    NIGHTLY_SHUTDOWN_MAX_MINUTES,
    NIGHTLY_SHUTDOWN_START_HOUR,
    NOMINAL_CYCLE_MINUTES,
)

def compute_gaps(df):
    """Minutes elapsed between each row and the previous row, in time order."""
    return df["timestamp"].diff().dt.total_seconds() / 60.0


def classify_gap(minutes, starts_at, ends_at):
    """
    Bucket a single gap. `starts_at` is the timestamp of the row BEFORE the gap
    and `ends_at` the row after, which is what makes the nightly test possible.
    """
    if minutes <= GAP_SUB_NOMINAL_MINUTES:
        return "sub-nominal (repeat transmission)"
    if minutes <= GAP_NOMINAL_MINUTES:
        return "nominal"

    # Scheduled building power-down: evening -> next morning, roughly 7 h.
    # Checked before severity so it never inflates the outage count.
    if (
        starts_at.hour >= NIGHTLY_SHUTDOWN_START_HOUR
        and ends_at.hour <= NIGHTLY_SHUTDOWN_END_HOUR
        and minutes <= NIGHTLY_SHUTDOWN_MAX_MINUTES
    ):
        return "scheduled overnight shutdown"

    if minutes <= GAP_MINOR_MINUTES:
        return "minor"
    if minutes <= GAP_MODERATE_MINUTES:
        return "moderate"
    if minutes <= GAP_MAJOR_MINUTES:
        return "major"
    return "critical"


def detect_outages(df, gaps):
    """
    Build one record per non-nominal gap.

    Every gap above the nominal cycle is recorded, including the scheduled
    overnight ones, so the classification is auditable rather than pre-filtered.
    Callers exclude 'scheduled overnight shutdown' when counting real outages.
    """
    previous = df["timestamp"].shift(1)
    records = []
    # Declared up front so a dataset with NO gaps still returns a frame with
    # these columns. An empty DataFrame() has no columns at all, and every
    # caller below selects on "severity".
    columns = [
        "gap_start", "gap_end", "gap_minutes", "gap_hours", "severity",
        "missed_transmissions", "count_before", "count_after",
    ]

    for i in range(1, len(df)):
        minutes = gaps.iloc[i]
        if pd.isna(minutes):
            continue

        # Nominal traffic is ~90% of all gaps and is not worth recording.
        # Sub-nominal repeats ARE recorded: they sit below the nominal band but
        # signal a fast-cycling node, so they are an anomaly, not clean data.
        is_nominal = GAP_SUB_NOMINAL_MINUTES < minutes <= GAP_NOMINAL_MINUTES
        if is_nominal:
            continue

        start = previous.iloc[i]
        end = df["timestamp"].iloc[i]
        severity = classify_gap(minutes, start, end)

        records.append(
            {
                "gap_start": start,
                "gap_end": end,
                "gap_minutes": round(minutes, 2),
                "gap_hours": round(minutes / 60.0, 3),
                "severity": severity,
                # How many scheduled transmissions the hole represents. Only
                # meaningful for real outages, so left blank for the scheduled
                # overnight window.
                "missed_transmissions": (
                    "" if severity == "scheduled overnight shutdown"
                    else max(0, int(round(minutes / NOMINAL_CYCLE_MINUTES)) - 1)
                ),
                "count_before": df["Count"].iloc[i - 1],
                "count_after": df["Count"].iloc[i],
            }
        )

    return pd.DataFrame(records, columns=columns)


def real_outages(outages):
    """Gaps that represent genuine data loss: minor and worse, not scheduled."""
    if outages.empty:
        return outages
    excluded = ["scheduled overnight shutdown", "sub-nominal (repeat transmission)"]
    return outages[~outages["severity"].isin(excluded)]


def significant_outages(outages):
    """
    Real outages of moderate severity or worse (> 30 min).

    Reported separately from the full real-outage count because the two mean
    very different things operationally: a 'minor' gap is a handful of dropped
    packets and there are ~1500 of them, while a moderate-or-worse gap means the
    station was actually absent. Quoting one headline number for both would let
    a 10-minute dropout and a 19-day outage weigh the same.
    """
    real = real_outages(outages)
    if real.empty:
        return real
    return real[real["severity"].isin(["moderate", "major", "critical"])]

