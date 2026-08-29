"""Console summaries and anomaly reporting for the reliability audit."""

import numpy as np
import pandas as pd

from audit_config import (
    BASELINE_CHANGEOVER_DATE,
    DAY_OVER_BASELINE_TOLERANCE,
    EXPECTED_TRANSMISSIONS_24H,
    EXPECTED_TRANSMISSIONS_POWERED,
    SENSOR_COLUMNS,
)
from outage_analysis import real_outages, significant_outages
from reliability_metrics import find_commissioning_date, verify_baseline_regimes


def report_gap_distribution(gaps):
    """
    Print the empirical inter-arrival distribution BEFORE thresholds are
    applied, so the constants above can be justified against it.
    """
    clean = gaps.dropna()
    print("=" * 78)
    print("EMPIRICAL GAP DISTRIBUTION (minutes between consecutive rows)")
    print("=" * 78)
    if clean.empty:
        # One row means no inter-arrival times exist; percentiles would raise.
        print("  Fewer than two rows -- no gaps to describe.")
        print()
        return
    print(f"  n = {len(clean)}   mean = {clean.mean():.2f}   median = {clean.median():.2f}")
    print()
    print("  percentile      minutes")
    for q in [1, 5, 25, 50, 75, 90, 95, 97, 98, 99, 99.5, 99.7, 99.9, 100]:
        print(f"    p{q:<10} {np.percentile(clean, q):12.2f}")
    print()
    print("  Most common gaps (rounded to 0.1 min) -- note the quantisation at")
    print("  multiples of the 5 min cycle, which is what motivates the thresholds:")
    for value, n in clean.round(1).value_counts().head(8).items():
        print(f"    {value:7.1f} min  x{n:6d}")
    print()


def summarise_outages(outages):
    """Print the severity breakdown and the worst real outages."""
    print("=" * 78)
    print("GAP / OUTAGE SEVERITY BREAKDOWN")
    print("=" * 78)

    order = [
        "sub-nominal (repeat transmission)",
        "minor",
        "moderate",
        "major",
        "critical",
        "scheduled overnight shutdown",
    ]
    counts = outages["severity"].value_counts()
    for severity in order:
        n = int(counts.get(severity, 0))
        note = " (expected, excluded from outage totals)" if "scheduled" in severity else ""
        print(f"  {severity:<36} {n:6d}{note}")

    real = real_outages(outages)
    significant = significant_outages(outages)
    print()
    print(f"  All real data-loss gaps (minor and worse)  : {len(real)}")
    print(f"  Significant outages (> 30 min, moderate+)  : {len(significant)}")
    if not real.empty:
        print(f"  Longest outage: {real['gap_hours'].max():.2f} h "
              f"({real['gap_minutes'].max() / 1440:.2f} days)")
        print()
        print("  Ten longest outages:")
        for _, row in real.nlargest(10, "gap_minutes").iterrows():
            print(
                f"    {row['gap_start']} -> {row['gap_end']}  "
                f"{row['gap_hours']:9.2f} h  {row['severity']}"
            )
    print()


def summarise_sensor_completeness(df):
    """Print overall and post-commissioning completeness for each sensor."""
    print("=" * 78)
    print("PER-SENSOR COMPLETENESS (within received rows only)")
    print("=" * 78)
    print(f"  {'sensor':<18} {'overall':>9} {'since commissioned':>20}  commissioned")
    print(f"  {'-' * 18} {'-' * 9} {'-' * 20}  {'-' * 12}")

    summary = {}
    total_rows = len(df)
    for column in SENSOR_COLUMNS:
        overall = df[column].notna().sum() / total_rows
        commissioned = find_commissioning_date(df, column)

        if commissioned is None:
            since = float("nan")
        else:
            after = df[df["timestamp"].dt.date >= commissioned]
            since = after[column].notna().sum() / len(after)

        summary[column] = {"overall": overall, "since_commissioned": since,
                           "commissioned": commissioned}
        print(
            f"  {column:<18} {100 * overall:8.1f}% {100 * since:19.1f}%  {commissioned}"
        )
    print()
    return summary


def summarise_daily_reliability(daily_reliability):
    """Print the day-class breakdown."""
    print("=" * 78)
    print("DAILY RELIABILITY CLASSIFICATION")
    print("=" * 78)
    print(f"  baseline = {EXPECTED_TRANSMISSIONS_24H}/day before "
          f"{BASELINE_CHANGEOVER_DATE} (24 h operation), "
          f"{EXPECTED_TRANSMISSIONS_POWERED}/day after (17 powered hours x 12/hour)")
    print()

    counts = daily_reliability["failure_class"].value_counts()
    total = len(daily_reliability)
    for label, n in counts.items():
        print(f"  {label:<52} {n:5d}  ({100 * n / total:5.1f}%)")
    print()


# --------------------------------------------------------------------------
# 7. Final summary
# --------------------------------------------------------------------------

def print_baseline_check(regimes):
    """Print the regime table and flag anything that contradicts the constants."""
    print("  Baseline check (night = hours 23,00-05; rates are rows per "
          "hour-of-day slot per day):")
    print(f"      {'regime':<22} {'expected':>9} {'night/h':>9} {'day/h':>8} "
          f"{'ratio':>7} {'peak day':>9}")
    for regime in regimes:
        print(f"      {regime['label']:<22} {regime['expected']:>7}/day "
              f"{regime['night_rate']:9.1f} {regime['day_rate']:8.1f} "
              f"{regime['ratio']:7.2f} {regime['peak_day']:9d}")

    # A 24 h regime should show night traffic comparable to daytime; a powered
    # regime should show almost none. Say so if the data disagrees.
    for regime in regimes:
        expects_night = regime["expected"] == EXPECTED_TRANSMISSIONS_24H
        if expects_night and regime["ratio"] < 0.5:
            print(f"      WARNING: {regime['label']} assumes 24 h operation but "
                  f"night traffic is only {regime['ratio']:.2f} of daytime")
        if not expects_night and regime["ratio"] > 0.1:
            print(f"      WARNING: {regime['label']} assumes a nightly shutdown "
                  f"but night traffic is {regime['ratio']:.2f} of daytime")


def print_reconciliation(reconciliation):
    """
    Print the expected/received/missed identity.

    Published so the two figures cannot silently disagree again. Before the
    schedule was centralised, the missed count assumed a 5-minute cycle around
    the clock while the daily denominator did not, and the two overshot the
    expected total by 3,811 transmissions (5.5%).
    """
    print("=" * 78)
    print("TRANSMISSION RECONCILIATION")
    print("=" * 78)
    print(f"  Expected (scheduled slots in span) : {reconciliation['expected']:>8,}")
    print(f"  Received (rows)                    : {reconciliation['received']:>8,}")
    print(f"  Missed   (empty scheduled slots)   : {reconciliation['missed']:>8,}")
    print(f"  Surplus  (rows in no new slot)     : {reconciliation['surplus']:>8,}"
          "   duplicates / sub-minute repeats")
    print(f"  {'-' * 37} {'-' * 8}")
    print(f"  received + missed - surplus        : "
          f"{reconciliation['received'] + reconciliation['missed'] - reconciliation['surplus']:>8,}")
    print(f"  Residual (must be 0)               : {reconciliation['residual']:>8,}")
    if reconciliation["residual"] != 0:
        print("      WARNING: the accounting does not balance; expected, missed and "
              "received are not measuring the same scope.")
    print()


def print_summary(df, validation, daily, daily_reliability, sensor_summary, outages,
                  reconciliation=None):
    """The headline summary block."""
    start = df["timestamp"].min()
    end = df["timestamp"].max()
    span_days = (end.date() - start.date()).days + 1

    total_expected = int(daily["expected_rows"].sum())
    overall_completeness = len(df) / total_expected

    real = real_outages(outages)
    significant = significant_outages(outages)
    days_with_no_data = int((daily["rows_received"] == 0).sum())

    # Only rank sensors that ever reported. A never-populated sensor has a NaN
    # rate, and NaN comparisons are always False, so leaving it in would let
    # min() return an arbitrary sensor rather than the worst one.
    ranked = {
        name: stats for name, stats in sensor_summary.items()
        if not pd.isna(stats["since_commissioned"])
    }
    worst_sensor = (
        min(ranked.items(), key=lambda kv: kv[1]["since_commissioned"])
        if ranked else None
    )

    regimes = verify_baseline_regimes(df)

    print("=" * 78)
    print("RELIABILITY AUDIT SUMMARY -- BWB Weather Station")
    print("=" * 78)
    print(f"  Dataset period          : {start.date()} to {end.date()}  ({span_days} days)")
    print(f"  Total observations      : {len(df):,} rows "
          f"({validation['rows_dropped']} dropped in validation)")
    days_24h = int((daily["expected_rows"] == EXPECTED_TRANSMISSIONS_24H).sum())
    days_powered = int((daily["expected_rows"] == EXPECTED_TRANSMISSIONS_POWERED).sum())
    # Daylight-saving days are neither 288 nor 204: the 25-hour fall-back day is
    # schedulable for 300 slots and the 23-hour spring-forward day for 276.
    dst_days = daily.loc[
        ~daily["expected_rows"].isin(
            [EXPECTED_TRANSMISSIONS_24H, EXPECTED_TRANSMISSIONS_POWERED]),
        "expected_rows",
    ]
    print(f"  Expected transmissions  : {total_expected:,} "
          f"({EXPECTED_TRANSMISSIONS_24H}/day x {days_24h} days + "
          f"{EXPECTED_TRANSMISSIONS_POWERED}/day x {days_powered} days"
          + (" + " + " + ".join(f"{int(n)} on 1 DST day" for n in sorted(dst_days))
             if len(dst_days) else "")
          + ")")
    print(f"  Overall row completeness: {100 * overall_completeness:.1f}%")
    if reconciliation is not None:
        print(f"  Missed transmissions    : {reconciliation['missed']:,} "
              f"(expected - received + surplus; reconciles exactly)")
    print()
    print_baseline_check(regimes)
    print()
    print(f"  Days with zero data     : {days_with_no_data} of {len(daily)} "
          f"({100 * days_with_no_data / len(daily):.1f}%)")
    print(f"  Significant outages     : {len(significant)} gaps > 30 min "
          f"(scheduled overnight shutdowns excluded)")
    print(f"  Minor dropouts          : {len(real) - len(significant)} gaps of "
          f"7.5-30 min (a few consecutive missed transmissions)")
    if not real.empty:
        longest = real.loc[real["gap_minutes"].idxmax()]
        print(f"  Longest outage          : {longest['gap_hours']:.1f} h "
              f"({longest['gap_minutes'] / 1440:.1f} days), "
              f"{longest['gap_start']} -> {longest['gap_end']}")
    print()
    print("  Lowest-completeness sensor (since commissioned):")
    if worst_sensor is None:
        print("      n/a -- no sensor reported a value in this dataset")
    else:
        print(f"      {worst_sensor[0]} at "
              f"{100 * worst_sensor[1]['since_commissioned']:.1f}%")
    print()

    print("  Notable anomalies (all unresolved -- see README Limitations):")
    for line in build_anomaly_notes(df, validation, daily_reliability, outages):
        print(f"    - {line}")
    print()


def build_anomaly_notes(df, validation, daily_reliability, outages):
    """
    Assemble the anomaly bullet list from what was actually measured.

    Every note is conditional on the finding being present in THIS dataset.
    Nothing here is hard-coded to the November 2025 - July 2026 export: running
    the audit on a clean file must not make it assert anomalies it did not find.
    """
    notes = []

    if validation["rows_dropped"]:
        notes.append(
            f"{validation['rows_dropped']} row(s) removed by the (timestamp, Count) "
            f"dedup rule; Count alone is NOT unique and was not used as a key"
        )

    for frame in validation["corrupted_frames"]:
        garbage = ", ".join(
            f"{column}={value:.0f} (0x{int(value):08X})"
            for column, value in frame["garbage"].items()
        )
        notes.append(
            f"corrupted frame at {frame['timestamp']} carrying {garbage}; "
            f"doGet.js range-checks none of these before writing"
        )

    if validation["backward_jumps"]:
        notes.append(
            f"{validation['backward_jumps']} backward timestamp jump(s) in file "
            f"order with Count still monotonic -- a receipt-clock artefact, not a "
            f"device fault"
        )

    zero_columns = [
        f"{column} ({int((df[column] == 0).sum())})"
        for column in SENSOR_COLUMNS
        if (df[column] == 0).sum() > 0
    ]
    if zero_columns:
        notes.append(
            "literal zeros survive in " + ", ".join(zero_columns)
            + " -- doGet.js has NOT blanked zeros uniformly across this dataset: "
              "zero-blanking was added 2025-12-16 and only covered Temperature "
              "from 2026-04-05, so a blank cell means 'NaN' in the early data and "
              "'NaN or exactly zero' later. 0 and 'missing' are conflated at "
              "source from those dates onward and cannot be separated downstream "
              "(see README, Ingestion-Behavior Changes)"
        )

    # Commissioning first: a sensor switched on mid-deployment also shows a huge
    # null-rate swing, and reporting both would say the same thing twice.
    commissioning_notes, explained = _commissioning_notes(df)
    notes.extend(_null_rate_swing_notes(df, skip=explained))
    notes.extend(commissioning_notes)

    over_baseline = int(
        (daily_reliability["failure_class"]
         == "Over-baseline (fast-cycling / repeat transmissions)").sum()
    )
    if over_baseline:
        notes.append(
            f"{over_baseline} day(s) exceed that day's physical maximum by more "
            f"than {100 * (DAY_OVER_BASELINE_TOLERANCE - 1):.0f}% "
            f"(max {int(daily_reliability['rows_received'].max())} rows) "
            f"-- fast-cycling, which the old in-sheet audit hid by capping at 100%"
        )

    repeats = int((outages["severity"] == "sub-nominal (repeat transmission)").sum())
    if repeats:
        notes.append(
            f"{repeats} sub-minute repeat transmissions (gap < 1 min), most with an "
            f"unchanged Count -- duplicated readings, not extra coverage"
        )

    if not notes:
        notes.append("none detected in this dataset")
    return notes


# A sensor whose monthly null rate ranges this widely has changed behaviour
# during the deployment rather than just dropping the odd reading.
NULL_RATE_SWING_THRESHOLD = 50.0


def _null_rate_swing_notes(df, skip=()):
    """
    Report sensors whose monthly null rate swings widely across the deployment.

    Generic rather than hard-coded to Soil Moisture: the point is to surface any
    sensor that fails and recovers, and to state the measured spread change
    rather than assert one. `skip` carries sensors already explained by a
    commissioning event, whose swing is not a fault.
    """
    notes = []
    monthly = df.set_index("timestamp")[SENSOR_COLUMNS].isna().resample("MS").mean() * 100

    for column in SENSOR_COLUMNS:
        if column in skip:
            continue
        rates = monthly[column].dropna()
        if len(rates) < 2 or (rates.max() - rates.min()) < NULL_RATE_SWING_THRESHOLD:
            continue

        note = (
            f"{column} null rate swings "
            + " -> ".join(f"{m.strftime('%b')} {v:.0f}%" for m, v in rates.items())
        )

        # Compare the spread of the first and last populated months, so the
        # "the signal itself changed" claim is measured, not assumed.
        populated = df.loc[df[column].notna(), ["timestamp", column]]
        if len(populated) > 2:
            # to_period() cannot carry a timezone; drop it explicitly rather than
            # letting pandas warn and discard it for us. These are local months.
            months = populated["timestamp"].dt.tz_localize(None).dt.to_period("M")
            first_month = months.min()
            last_month = months.max()
            if first_month != last_month:
                early = populated.loc[months == first_month, column]
                late = populated.loc[months == last_month, column]
                if len(early) > 1 and len(late) > 1:
                    note += (
                        f"; spread also changes (std {early.std():.0f} in "
                        f"{first_month} vs {late.std():.0f} in {last_month})"
                    )
        notes.append(note + ". Not imputed, not normalised")
    return notes


def _commissioning_notes(df):
    """
    Report sensors that never reported, or that started reporting late.

    Generic rather than hard-coded to Battery Voltage. A sensor that begins
    mid-deployment is a feature being switched on, not a sensor failing, and
    must not be read as a reliability problem.

    Returns (notes, explained) where `explained` names the sensors whose missing
    data is accounted for here, so the caller does not also report their
    null-rate swing as a separate fault.
    """
    notes = []
    explained = set()
    first_day = df["timestamp"].min().date()

    for column in SENSOR_COLUMNS:
        commissioned = find_commissioning_date(df, column)
        if commissioned is None:
            notes.append(
                f"{column} is empty for the whole period -- never populated, so no "
                f"completeness figure is meaningful for it"
            )
            explained.add(column)
            continue

        # A few days' delay is ordinary; a month is a commissioning event.
        if (commissioned - first_day).days >= 28:
            after = df[df["timestamp"].dt.date >= commissioned]
            notes.append(
                f"{column} reports nothing before {commissioned} then is "
                f"{100 * after[column].notna().mean():.0f}% populated -- a feature "
                f"commissioned mid-deployment, NOT a failing sensor"
            )
            explained.add(column)
    return notes, explained
