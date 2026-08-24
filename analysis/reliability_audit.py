"""
Reliability Audit for the BWB Weather Station
=============================================

Chain under audit:
    XIAO ESP32C3 transmitter --ESP-NOW--> XIAO ESP32C3 receiver --I2C-->
    second ESP32 --school Wi-Fi--> Apps Script (doGet.js) --> Google Sheets
    --> HistoricalData sheet --> CSV export (this script's input)

The audit answers two questions that are deliberately kept SEPARATE:

    1. ROW / SYSTEM completeness  -- did a transmission arrive at all?
    2. SENSOR   completeness      -- within rows that DID arrive, was a given
                                     field populated?

Conflating these is the central methodological error this script avoids. A dead
soil moisture probe is not a station outage, and a station outage is not a
sensor fault. They are computed, reported and plotted independently.

Usage:
    python reliability_audit.py <path_to_csv> [output_dir]

Outputs (written to output_dir, default ./audit_output):
    outage_intervals.csv     one row per detected gap above the nominal cycle
    sensor_completeness.csv  daily per-sensor field population rates
    daily_reliability.csv    daily row completeness + failure classification
    plot_daily_completeness.png
    plot_daily_largest_gap.png
    plot_sensor_completeness.png
    plot_gap_distribution.png
"""

import sys
import os
from datetime import date

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")  # no display on the machines this runs on
import matplotlib.dates as mdates  # noqa: E402  (backend must be set first)
import matplotlib.pyplot as plt  # noqa: E402


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

# The transmitter deep-sleeps for 300 s between transmissions.
NOMINAL_CYCLE_MINUTES = 5.0

# THE BASELINE IS NOT CONSTANT. It changed partway through the deployment, so a
# single denominator would be wrong for most of the dataset.
#
#   Until 2026-04-20  the station ran 24 h/day    -> 24*60/5      = 288/day
#   From  2026-04-21  the building power is cut
#                     ~23:00-06:00, leaving 17
#                     powered hours               -> 17*12        = 204/day
#
# The changeover date is empirically pinned, not assumed:
#   * 2026-04-20 is the last day with night-hour traffic (71 rows in hours
#     23,00-05). From 2026-04-21 onward there are exactly ZERO night-hour rows
#     for the remaining 100 days, bar two 23:00:0x rows that sit right on the
#     cut-off boundary.
#   * night-vs-day rows per hour-slot runs at a ratio of ~1.0 every month from
#     Nov to Apr (0.89-1.14), then collapses to 0.00-0.01 from May onward.
#   * peak daily row counts track the two ceilings: 293/573/291/286/289 in
#     Nov-Apr, then 205/199/200 in May/Jun/Jul.
#   * the ~426-minute overnight gaps begin on 2026-04-20/21 and recur nightly
#     thereafter (13 in May, 19 in Jun, 30 in Jul).
#
# verify_baseline_regimes() re-checks all of this on every run, so if the
# schedule changes again the audit says so instead of silently misreporting.
BASELINE_CHANGEOVER_DATE = date(2026, 4, 21)
EXPECTED_TRANSMISSIONS_24H = 288       # before the changeover
EXPECTED_TRANSMISSIONS_POWERED = 204   # after: 17 powered hours * 12/hour

ACTIVE_HOUR_START = 6   # first hour of the powered window (inclusive)
ACTIVE_HOUR_END = 22    # last  hour of the powered window (inclusive)
NIGHT_HOURS = [23, 0, 1, 2, 3, 4, 5]   # the hours lost after the changeover


def expected_transmissions_for(day):
    """
    Scheduled transmissions for one calendar day.

    Kept as a function rather than a constant because the station's duty cycle
    changed mid-deployment; see BASELINE_CHANGEOVER_DATE above.
    """
    if day < BASELINE_CHANGEOVER_DATE:
        return EXPECTED_TRANSMISSIONS_24H
    return EXPECTED_TRANSMISSIONS_POWERED

# The timestamp column is named "Date" in the HistoricalData export even though
# it holds a full datetime (it is the Apps Script receipt time, not the sensor
# read time). Older exports/briefs call it "Timestamp", so accept either.
TIMESTAMP_COLUMN_CANDIDATES = ["Date", "Timestamp"]

SENSOR_COLUMNS = [
    "Temperature",
    "Humidity",
    "Soil Moisture",
    "Air Pressure",
    "Rain Value",
    "Battery Voltage",
]

# "Core" sensors = the ones present for the whole deployment and expected to
# work. Soil Moisture and Battery Voltage are excluded from the core set on
# purpose: Soil Moisture has an unresolved multi-month failure (see README) and
# Battery Voltage was only commissioned in April 2026. Including either would
# drag the day classification down for reasons that are not transmission faults.
CORE_SENSOR_COLUMNS = ["Temperature", "Humidity", "Air Pressure", "Rain Value"]

# Physically plausible ranges, used ONLY to flag corrupted values, never to
# silently clean data. Soil Moisture and Rain Value are raw 12-bit ADC counts.
PLAUSIBLE_SENSOR_RANGES = {
    "Temperature": (-50.0, 60.0),        # deg C
    "Humidity": (0.0, 100.0),            # %
    "Soil Moisture": (0.0, 4095.0),      # 12-bit ADC
    "Air Pressure": (800.0, 1100.0),     # hPa
    "Rain Value": (0.0, 4095.0),         # 12-bit ADC
    "Battery Voltage": (0.0, 6000.0),    # mV
}

# Count is an RTC_DATA_ATTR boot counter. It survives deep sleep but resets to 0
# on a full power-loss reboot, so it is monotonic only WITHIN a reboot epoch and
# is NOT unique across the file. Anything above this ceiling is memory garbage,
# not a real boot count (the known bad row carries 939531320 == 0x38001C38).
MAX_PLAUSIBLE_COUNT = 1_000_000

# --- Gap severity thresholds -------------------------------------------------
#
# These are derived from the empirical inter-arrival distribution of THIS
# dataset (printed by report_gap_distribution() before any thresholds are
# applied, so the reasoning below can be re-checked against new data).
#
# The observed distribution is strongly quantised at multiples of the 5-minute
# cycle, because a lost transmission does not shift the schedule -- it just
# leaves a hole. Observed percentiles (minutes):
#
#     p50  4.97      p95   9.97      p99    39.72
#     p75  4.98      p97  10.03      p99.5 119.21
#     p90  5.02      p98  15.00      p99.7 424.26     max 27328 (19.0 d)
#
# The thresholds are therefore set at CYCLE MULTIPLES and at the natural breaks
# in the tail, not at round clock numbers:
#
#   1.5 cycles (7.5 min)  -- separates nominal jitter from a genuine miss. p90
#                            is 5.02, so anything past 7.5 min is a real hole.
#   6 cycles   (30 min)   -- just past p99 (39.7 min is 8 cycles). Below this a
#                            gap is a handful of dropped packets; above it the
#                            station was meaningfully absent.
#   24 cycles  (120 min)  -- p99.5 is 119.21 min, i.e. this is the empirical
#                            0.5% tail boundary, not a chosen round number.
#   480 min    (8 h)      -- the sharpest discontinuity in the whole tail:
#                            104 gaps exceed 6 h but only 37 exceed 8 h. That
#                            cliff is the scheduled overnight shutdown, which
#                            clusters tightly at ~424 min (7.07 h, IQR
#                            424-429). 8 h is the first duration that cannot be
#                            explained by the building being switched off.
#
# A gap below the nominal cycle is its own anomaly: 758 gaps are under 1 minute,
# and 556 of those carry an unchanged Count. Those are repeat transmissions from
# a rebooting/fast-cycling node, not extra data, so they are flagged rather than
# counted as good coverage.
GAP_SUB_NOMINAL_MINUTES = 1.0
GAP_NOMINAL_MINUTES = 7.5      # 1.5 cycles
GAP_MINOR_MINUTES = 30.0       # 6 cycles
GAP_MODERATE_MINUTES = 120.0   # 24 cycles, ~p99.5
GAP_MAJOR_MINUTES = 480.0      # 8 h, beyond any scheduled shutdown

# A gap that starts in the late evening and ends early the next morning is the
# scheduled building power-down, not a fault. Detected by shape rather than by
# assuming a fixed clock window, because the switch-off time is not exact.
NIGHTLY_SHUTDOWN_START_HOUR = 22   # gap begins at or after this hour
NIGHTLY_SHUTDOWN_END_HOUR = 7      # gap ends at or before this hour
NIGHTLY_SHUTDOWN_MAX_MINUTES = 600  # 10 h ceiling; the real cluster is ~7.07 h

# --- Daily classification thresholds ----------------------------------------
#
# Structure inherited from the deprecated Apps Script
# deprecatedReliabilityAudit.js, but with three changes:
#   * the denominator is per-day (288 or 204) instead of a fixed 288.
#   * an OVER-BASELINE class, because 2 days exceed their own baseline by more
#     than the jitter tolerance (419 and 573 rows against 288). The old script
#     capped completeness at 100%, which HID that. More rows than physically
#     schedulable means the node was fast-cycling -- a malfunction that used to
#     be reported as a perfect day.
#   * the sensor-level check uses CORE_SENSOR_COLUMNS only (see above).
# How far past the baseline a day must go before it counts as fast-cycling.
# The observed excesses split into two clearly separated groups: ten days sit at
# 289-293 rows against a 288 baseline (and one at 205 against 204), i.e. +1 to +5
# rows or <= 101.8% -- that is a transmission landing either side of midnight,
# not a fault. The genuine cases are far away at 145% and 199%. Anything inside
# the jitter band is classified on its merits instead of being called a
# malfunction.
DAY_OVER_BASELINE_TOLERANCE = 1.10

DAY_SEVERE_COMPLETENESS = 0.25
DAY_PARTIAL_COMPLETENESS = 0.75
DAY_GOOD_COMPLETENESS = 0.95
DAY_CORE_SENSOR_COMPLETENESS = 0.75


# --------------------------------------------------------------------------
# 1. Load and validate
# --------------------------------------------------------------------------

def resolve_timestamp_column(df):
    """Return whichever of the accepted timestamp column names is present."""
    for name in TIMESTAMP_COLUMN_CANDIDATES:
        if name in df.columns:
            return name
    raise SystemExit(
        f"No timestamp column found. Expected one of {TIMESTAMP_COLUMN_CANDIDATES}, "
        f"got {list(df.columns)}"
    )


def require_expected_columns(df):
    """
    Fail early and clearly if the export is missing columns the audit needs.

    Without this the first missing sensor surfaces as a bare KeyError from deep
    inside pandas, which says nothing about which column or which file.
    """
    missing = [c for c in SENSOR_COLUMNS + ["Count"] if c not in df.columns]
    if missing:
        raise SystemExit(
            "CSV is missing required column(s): " + ", ".join(missing)
            + f"\nExpected: {', '.join(SENSOR_COLUMNS + ['Count'])}"
        )


def flag_corrupted_frame(df, log):
    """
    Null every field on rows whose Count is memory garbage, keeping the ROW.

    The row was genuinely received, so it still counts towards row completeness
    -- that is the row-level/sensor-level separation in action. What it cannot
    do is contribute values, because the whole frame is corrupt: the known bad
    row carries Count=939531320 (0x38001C38) AND Soil Moisture=469762076
    (0x1C00001C), two bit patterns from the same damaged buffer. doGet.js
    range-checks nothing before writing, which is how they reach the sheet.

    Count drives reboot/epoch detection, so leaving it in place would invent a
    reboot that never happened.
    """
    corrupted = df["Count"] > MAX_PLAUSIBLE_COUNT
    log.append(f"Corrupted frames found (row kept, all fields nulled): {int(corrupted.sum())}")

    details = []
    for _, row in df.loc[corrupted].iterrows():
        details.append({
            "timestamp": row["timestamp"],
            "garbage": {
                column: row[column]
                for column in ["Count"] + SENSOR_COLUMNS
                if pd.notna(row[column]) and row[column] > MAX_PLAUSIBLE_COUNT
            },
        })
        garbage = [
            f"{column}={row[column]:.0f} (0x{int(row[column]):08X})"
            for column in ["Count"] + SENSOR_COLUMNS
            if pd.notna(row[column]) and row[column] > MAX_PLAUSIBLE_COUNT
        ]
        log.append(
            f"    file row {int(row['file_order'])} @ {row['timestamp']}: "
            + ", ".join(garbage)
        )
        # Say out loud which non-garbage fields are being discarded too, so the
        # null counts downstream can be reconciled against the raw file.
        collateral = [
            column for column in SENSOR_COLUMNS
            if pd.notna(row[column]) and row[column] <= MAX_PLAUSIBLE_COUNT
        ]
        if collateral:
            log.append(
                "        also nulled on this row (same corrupt frame): "
                + ", ".join(f"{c}={row[c]}" for c in collateral)
            )

    df.loc[corrupted, SENSOR_COLUMNS + ["Count"]] = np.nan
    return df, details


def report_implausible_values(df, log, max_examples=3):
    """
    Report readings outside physically possible ranges WITHOUT changing them.

    Deliberately non-mutating. A value that is present but physically impossible
    is a third state beyond "row received" and "field populated", and silently
    nulling it would quietly move it into the "missing" bucket and change every
    completeness figure this audit reports. It is surfaced here and left in the
    data for a human to decide on.

    Episodes are collapsed to a count plus a date range because these arrive in
    long consecutive runs -- a stuck sensor, not scattered bad samples.
    """
    log.append("")
    log.append("-- implausible values (reported only, NOT modified) --")
    any_found = False

    for column, (low, high) in PLAUSIBLE_SENSOR_RANGES.items():
        bad = df[column].notna() & ((df[column] < low) | (df[column] > high))
        if not bad.any():
            continue

        any_found = True
        affected = df.loc[bad]
        log.append(
            f"    {column}: {int(bad.sum())} value(s) outside [{low}, {high}], "
            f"{affected['timestamp'].min()} to {affected['timestamp'].max()}"
        )
        for value, n in affected[column].value_counts().head(max_examples).items():
            log.append(f"        value {value:g} x{n}")
        distinct = affected[column].nunique()
        if distinct > max_examples:
            log.append(f"        ... and {distinct - max_examples} other distinct value(s)")

    if not any_found:
        log.append("    none")
    return df


def deduplicate_exact_repeats(df, log):
    """
    Drop rows that repeat BOTH timestamp and Count, keeping the first in file
    order.

    Count alone is not a valid dedup key. It is RTC_DATA_ATTR, so it resets on
    power loss and repeats across reboot epochs -- 20,402 of 24,835 rows share a
    Count with some other row. Deduplicating on Count would destroy 82% of the
    dataset. Only the (timestamp, Count) pair is specific enough.
    """
    key = ["timestamp", "Count"]
    duplicated = df.duplicated(subset=key, keep="first")

    if duplicated.any():
        log.append(f"Exact (timestamp, Count) duplicates dropped: {int(duplicated.sum())}")
        # Show the full duplicate groups, not just the dropped half, so the
        # differing sensor values are visible in the log.
        groups = df.duplicated(subset=key, keep=False)
        for _, row in df.loc[groups].iterrows():
            kept = "KEPT   " if not duplicated.loc[row.name] else "DROPPED"
            log.append(
                f"    {kept} file row {int(row['file_order'])} @ {row['timestamp']} "
                f"Count={row['Count']:.0f} Temp={row['Temperature']} "
                f"Soil={row['Soil Moisture']}"
            )
    else:
        log.append("Exact (timestamp, Count) duplicates dropped: 0")

    return df.loc[~duplicated].copy()


def detect_backward_timestamp_jumps(df, log):
    """
    Report rows whose timestamp moves backwards relative to FILE order.

    Must run before any sort, otherwise sorting silently repairs the symptom and
    the anomaly disappears from the report. Count staying monotonic across the
    jump shows the transmitter was fine and only the Apps Script receipt clock
    moved, so the affected rows are kept as-is.
    """
    went_backwards = df["timestamp"].diff() < pd.Timedelta(0)
    count = int(went_backwards.sum())
    log.append(f"Backward timestamp jumps in file order: {count}")

    for position in df.index[went_backwards]:
        window = df.loc[position - 1: position + 1]
        for _, row in window.iterrows():
            log.append(
                f"    file row {int(row['file_order'])} @ {row['timestamp']} "
                f"Count={row['Count']:.0f}"
            )
    return count


def load_and_validate_data(path):
    """
    Read the HistoricalData CSV and run every integrity check, printing a
    validation summary.

    Order matters: file order is preserved and the backward-jump check runs
    BEFORE the timestamp sort, so the jump is reported instead of masked.

    Returns (df, validation) where df is sorted by timestamp.
    """
    if not os.path.exists(path):
        raise SystemExit(f"CSV not found: {path}")

    raw = pd.read_csv(path)
    timestamp_column = resolve_timestamp_column(raw)
    require_expected_columns(raw)
    raw = raw.rename(columns={timestamp_column: "timestamp"})
    raw["timestamp"] = pd.to_datetime(raw["timestamp"], errors="coerce")

    log = []
    validation = {}

    log.append(f"Source file            : {path}")
    log.append(f"Timestamp column       : '{timestamp_column}'")
    log.append(f"Rows read              : {len(raw)}")

    unparseable = int(np.count_nonzero(raw["timestamp"].isna()))
    log.append(f"Unparseable timestamps : {unparseable}")
    if unparseable:
        raw = raw.dropna(subset=["timestamp"])

    # Keep the original position so every later message can point back at the
    # actual line in the CSV.
    raw = raw.reset_index(drop=True)
    raw["file_order"] = np.arange(len(raw))

    log.append("")
    log.append("-- integrity checks (file order preserved) --")
    backward_jumps = detect_backward_timestamp_jumps(raw, log)
    raw, corrupted_frames = flag_corrupted_frame(raw, log)
    df = deduplicate_exact_repeats(raw, log)
    report_implausible_values(df, log)

    rows_dropped = len(raw) - len(df)

    if df.empty:
        print("\n".join(log))
        raise SystemExit(
            f"\nNo usable rows in {path} -- nothing to audit."
        )

    log.append("")
    log.append("-- null counts after validation --")
    for column in SENSOR_COLUMNS + ["Count"]:
        nulls = int(df[column].isna().sum())
        log.append(f"    {column:<18} {nulls:6d}  ({100 * nulls / len(df):5.1f}%)")

    # Count is not a lifetime-unique id; state that explicitly so nobody later
    # mistakes the duplicate figure for a data error.
    shares_count = int(df.duplicated("Count", keep=False).sum())
    log.append("")
    log.append(
        f"Rows sharing a Count value with another row: {shares_count} "
        f"({100 * shares_count / len(df):.1f}%) -- expected, Count resets on "
        f"power loss and is NOT a unique id"
    )

    # Sort for all downstream time-series analysis.
    df = df.sort_values("timestamp").reset_index(drop=True)
    if backward_jumps:
        log.append(
            f"NOTE: rows were reordered by timestamp for gap/outage analysis "
            f"({backward_jumps} backward jump(s) reported above)."
        )

    log.append("")
    log.append(f"Rows after validation  : {len(df)}  ({rows_dropped} dropped)")
    log.append(
        f"Period                 : {df['timestamp'].min()} to {df['timestamp'].max()}"
    )

    print("=" * 78)
    print("DATA VALIDATION")
    print("=" * 78)
    for line in log:
        print(line)
    print()

    validation["rows_read"] = len(raw)
    validation["rows_after"] = len(df)
    validation["rows_dropped"] = rows_dropped
    validation["backward_jumps"] = backward_jumps
    validation["corrupted_frames"] = corrupted_frames
    return df, validation


# --------------------------------------------------------------------------
# 2. Gap distribution and outage detection
# --------------------------------------------------------------------------

def compute_gaps(df):
    """Minutes elapsed between each row and the previous row, in time order."""
    return df["timestamp"].diff().dt.total_seconds() / 60.0


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


# --------------------------------------------------------------------------
# 3. Daily row completeness (system level)
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# 4. Per-sensor completeness (field level, within received rows)
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# 5. Daily reliability classification
# --------------------------------------------------------------------------

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
# 6. Plots
# --------------------------------------------------------------------------

CLASS_COLOURS = {
    "Good day": "#2e7d32",
    "Minor transmission loss": "#9ccc65",
    "Sensor-level issue": "#1e88e5",
    "Partial transmission loss": "#fbc02d",
    "Severe transmission loss": "#ef6c00",
    "Over-baseline (fast-cycling / repeat transmissions)": "#8e24aa",
    "Full outage": "#c62828",
}


def plot_daily_completeness(daily_reliability, output_dir):
    """Daily row completeness over time, coloured by failure class."""
    fig, ax = plt.subplots(figsize=(15, 6))
    dates = pd.to_datetime(daily_reliability["date"])

    for label, colour in CLASS_COLOURS.items():
        # "Full outage" always has zero rows, so its bar has zero height and can
        # never be seen. It is drawn as a rug below instead; skipping it here
        # keeps it out of the legend twice.
        if label == "Full outage":
            continue
        mask = daily_reliability["failure_class"] == label
        if not mask.any():
            continue
        ax.bar(dates[mask], 100 * daily_reliability.loc[mask, "row_completeness_raw"],
               color=colour, width=1.0, label=label)

    # A zero-row day is a zero-height bar, i.e. invisible -- and zero-row days
    # are the single largest category in this dataset. Draw them as a rug below
    # the axis so the most severe class is not the least visible one.
    no_data = daily_reliability["rows_received"] == 0
    if no_data.any():
        ax.scatter(
            dates[no_data], np.full(int(no_data.sum()), -7),
            marker="|", s=70, linewidths=1.4, color=CLASS_COLOURS["Full outage"],
            label=f"Full outage, zero rows ({int(no_data.sum())} days)",
            clip_on=False,
        )

    ax.axhline(100, color="black", linewidth=1, linestyle="--",
               label=f"baseline ({EXPECTED_TRANSMISSIONS_24H}/day, then "
                     f"{EXPECTED_TRANSMISSIONS_POWERED}/day)")
    # The denominator changes here, so the y axis means something different
    # either side of this line. Mark it rather than leaving it implicit.
    # date2num gives matplotlib the float it actually wants on a date axis.
    ax.axvline(mdates.date2num(BASELINE_CHANGEOVER_DATE), color="#37474f",
               linewidth=1.2, linestyle="-.",
               label=f"baseline changeover {BASELINE_CHANGEOVER_DATE}")
    ax.axhline(100 * DAY_GOOD_COMPLETENESS, color="#2e7d32", linewidth=0.8,
               linestyle=":", label=f"good day >= {100 * DAY_GOOD_COMPLETENESS:.0f}%")
    ax.set_ylim(bottom=-12)

    ax.set_title(
        "Daily row completeness (system level)\n"
        f"share of the physically schedulable transmissions per day that arrived "
        f"({EXPECTED_TRANSMISSIONS_24H}/day until {BASELINE_CHANGEOVER_DATE}, "
        f"{EXPECTED_TRANSMISSIONS_POWERED}/day after)",
        fontsize=12,
    )
    ax.set_ylabel("row completeness (%)")
    ax.set_xlabel("date")
    ax.legend(fontsize=8, ncol=2, loc="upper left")
    ax.margins(x=0.01)
    fig.tight_layout()

    path = os.path.join(output_dir, "plot_daily_completeness.png")
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def plot_daily_largest_gap(daily_reliability, output_dir):
    """Largest real outage per day, on a log scale with the severity bands."""
    fig, ax = plt.subplots(figsize=(15, 6))
    dates = pd.to_datetime(daily_reliability["date"])
    gaps = daily_reliability["largest_gap_minutes"].replace(0, np.nan)

    ax.scatter(dates, gaps, s=14, color="#c62828", alpha=0.75)
    ax.set_yscale("log")

    for value, label, colour in [
        (GAP_NOMINAL_MINUTES, "nominal ceiling (7.5 min)", "#9e9e9e"),
        (GAP_MINOR_MINUTES, "minor / moderate (30 min)", "#fbc02d"),
        (GAP_MODERATE_MINUTES, "moderate / major (2 h)", "#ef6c00"),
        (GAP_MAJOR_MINUTES, "major / critical (8 h)", "#c62828"),
    ]:
        ax.axhline(value, linestyle="--", linewidth=0.9, color=colour, label=label)

    ax.set_title(
        "Largest real outage per day (scheduled overnight shutdowns excluded)",
        fontsize=12,
    )
    ax.set_ylabel("gap length (minutes, log scale)")
    ax.set_xlabel("date")
    ax.legend(fontsize=8, loc="upper left")
    ax.margins(x=0.01)
    fig.tight_layout()

    path = os.path.join(output_dir, "plot_daily_largest_gap.png")
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def plot_sensor_completeness(sensor_daily, output_dir):
    """
    Per-sensor field population over time, with the soil moisture anomaly and
    the battery commissioning date called out visually.

    Series are reindexed onto the full calendar so that days with NO rows at all
    become breaks in the line. Without this, matplotlib draws a straight segment
    across a 19-day outage and it reads as continuous healthy data.
    """
    fig, ax = plt.subplots(figsize=(15, 6.5))
    calendar = pd.date_range(
        sensor_daily["date"].min(), sensor_daily["date"].max(), freq="D"
    )
    data_days = pd.to_datetime(sensor_daily["date"])

    styles = {
        "Temperature": ("#e53935", 1.2),
        "Humidity": ("#1e88e5", 1.2),
        "Air Pressure": ("#43a047", 1.2),
        "Rain Value": ("#6d4c41", 1.2),
        "Soil Moisture": ("#8e24aa", 2.2),
        "Battery Voltage": ("#fb8c00", 2.2),
    }

    smoothed = {}
    for column, (colour, width) in styles.items():
        # 7-day rolling mean over the days that actually have rows: daily rates
        # are very noisy on low-row days and the point of this plot is the
        # multi-month shape, not day-to-day jitter. Reindexing afterwards keeps
        # the smoothing while still showing the holes.
        series = pd.Series(
            (100 * sensor_daily[f"{column} completeness"]).to_numpy(),
            index=data_days,
        )
        rolled = pd.Series(series.rolling(7, min_periods=1).mean())
        series = rolled.reindex(calendar)
        smoothed[column] = series
        ax.plot(calendar, series, color=colour, linewidth=width, label=column)

    _shade_no_data_days(ax, calendar, data_days)
    _annotate_soil_moisture_anomaly(ax, smoothed["Soil Moisture"])

    ax.set_title(
        "Per-sensor completeness within received rows (7-day rolling mean)\n"
        "denominator is rows actually received that day, NOT the daily "
        "transmission baseline; grey bands = no rows received at all",
        fontsize=12,
    )
    ax.set_ylabel("field populated (%)")
    ax.set_xlabel("date")
    ax.set_ylim(-6, 122)
    ax.set_yticks([0, 20, 40, 60, 80, 100])
    # Legend below the axes: every horizontal band of this plot carries data
    # (0% for a dead sensor, 100% for a healthy one), so an inset legend would
    # cover exactly the thing being read.
    ax.legend(fontsize=8, ncol=6, loc="upper center",
              bbox_to_anchor=(0.5, -0.12), frameon=False)
    ax.margins(x=0.01)
    fig.tight_layout()

    path = os.path.join(output_dir, "plot_sensor_completeness.png")
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def _shade_no_data_days(ax, calendar, data_days):
    """Grey out calendar days on which no row was received at all."""
    missing = ~calendar.isin(data_days)
    ax.fill_between(
        calendar, -6, 122, where=missing,
        color="#9e9e9e", alpha=0.16, linewidth=0, zorder=0,
    )


def _annotate_soil_moisture_anomaly(ax, soil_series):
    """
    Mark the stretch where Soil Moisture was mostly absent.

    Drawn as a bar in the headroom above the data rather than as a full-height
    axvspan, because the no-data bands are already shaded and two overlapping
    spans compound into a colour that reads as a third category.

    Bounded by the first and last day whose 7-day rolling completeness is below
    50%, so the marker follows the data rather than hard-coded dates.
    """
    failing = soil_series < 50
    if not failing.any():
        return

    failing_days = soil_series.index[failing]
    start, end = failing_days.min(), failing_days.max()

    ax.plot([start, end], [113, 113], color="#8e24aa", linewidth=5,
            solid_capstyle="butt")
    ax.text(
        start, 116,
        "Soil Moisture: unresolved multi-month failure and recovery (see README)",
        fontsize=8, color="#6a1b9a", va="bottom",
    )


def plot_gap_distribution(gaps, output_dir):
    """
    Histogram of inter-arrival times with the chosen thresholds drawn on, so the
    thresholds can be checked against the distribution they came from.
    """
    fig, (ax_short, ax_tail) = plt.subplots(1, 2, figsize=(15, 5.5))
    clean = gaps.dropna()

    ax_short.hist(clean[clean <= 45], bins=90, color="#455a64")
    ax_short.set_yscale("log")
    ax_short.axvline(GAP_NOMINAL_MINUTES, color="#9e9e9e", linestyle="--",
                     label="nominal ceiling 7.5 min")
    ax_short.axvline(GAP_MINOR_MINUTES, color="#fbc02d", linestyle="--",
                     label="minor ceiling 30 min")
    for cycle in range(1, 10):
        ax_short.axvline(cycle * NOMINAL_CYCLE_MINUTES, color="#90caf9",
                         linewidth=0.6, alpha=0.7, zorder=0)
    ax_short.set_title("Short gaps (<= 45 min)\nblue lines mark 5 min cycle multiples",
                       fontsize=11)
    ax_short.set_xlabel("gap (minutes)")
    ax_short.set_ylabel("count (log)")
    ax_short.legend(fontsize=8)

    tail = clean[clean > GAP_NOMINAL_MINUTES]
    ax_tail.hist(np.log10(tail), bins=60, color="#455a64")
    for value, colour, label in [
        (GAP_MINOR_MINUTES, "#fbc02d", "minor / moderate (30 min)"),
        (GAP_MODERATE_MINUTES, "#ef6c00", "moderate / major (2 h)"),
        (GAP_MAJOR_MINUTES, "#c62828", "major / critical (8 h)"),
    ]:
        ax_tail.axvline(np.log10(value), color=colour, linestyle="--", label=label)

    # The scheduled overnight shutdown is a real mode in this distribution and it
    # is what puts the cliff just below the 8 h threshold. Label it so the
    # threshold choice is visible rather than asserted in a comment.
    ax_tail.annotate(
        "scheduled overnight\nshutdown (~7.1 h)",
        xy=(np.log10(424), 60), xytext=(np.log10(424) - 0.95, 240),
        fontsize=8, color="#37474f", ha="center",
        arrowprops={"arrowstyle": "->", "color": "#37474f", "linewidth": 0.8},
    )

    # Label the log axis in durations people can read, not raw log10 values.
    ticks = [10, 30, 60, 120, 480, 1440, 10080, 27328]
    labels = ["10 m", "30 m", "1 h", "2 h", "8 h", "1 d", "1 wk", "19 d"]
    ax_tail.set_xticks([np.log10(t) for t in ticks])
    ax_tail.set_xticklabels(labels, fontsize=8)
    ax_tail.set_title("Tail of the gap distribution (> 7.5 min)\nseverity thresholds marked",
                      fontsize=11)
    ax_tail.set_xlabel("gap length (log scale)")
    ax_tail.set_ylabel("count")
    ax_tail.legend(fontsize=8)

    fig.tight_layout()
    path = os.path.join(output_dir, "plot_gap_distribution.png")
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


# --------------------------------------------------------------------------
# 7. Final summary
# --------------------------------------------------------------------------

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
        # Per hour-of-day slot, so the two windows are directly comparable
        # despite covering 7 and 17 hours respectively.
        night_rate = night_rows / len(NIGHT_HOURS)
        day_rate = (len(rows) - night_rows) / (24 - len(NIGHT_HOURS))

        regimes.append({
            "label": label,
            "expected": expected,
            "night_rate": night_rate,
            "day_rate": day_rate,
            "ratio": night_rate / day_rate if day_rate else float("nan"),
            "peak_day": int(rows.groupby(day[mask]).size().max()),
        })
    return regimes


def print_baseline_check(regimes):
    """Print the regime table and flag anything that contradicts the constants."""
    print("  Baseline check (night = hours 23,00-05; rates are rows per "
          "hour-of-day slot):")
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


def print_summary(df, validation, daily, daily_reliability, sensor_summary, outages):
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
    days_powered = len(daily) - days_24h
    print(f"  Expected transmissions  : {total_expected:,} "
          f"({EXPECTED_TRANSMISSIONS_24H}/day x {days_24h} days + "
          f"{EXPECTED_TRANSMISSIONS_POWERED}/day x {days_powered} days)")
    print(f"  Overall row completeness: {100 * overall_completeness:.1f}%")
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
            + " even though doGet.js blanks 0 before writing -- 0 and 'missing' "
              "are conflated at source and cannot be separated downstream"
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
            first_month = populated["timestamp"].dt.to_period("M").min()
            last_month = populated["timestamp"].dt.to_period("M").max()
            if first_month != last_month:
                early = populated.loc[
                    populated["timestamp"].dt.to_period("M") == first_month, column]
                late = populated.loc[
                    populated["timestamp"].dt.to_period("M") == last_month, column]
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


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        raise SystemExit(
            "Usage: python reliability_audit.py <path_to_csv> [output_dir]"
        )

    csv_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "audit_output"
    os.makedirs(output_dir, exist_ok=True)

    df, validation = load_and_validate_data(csv_path)

    gaps = compute_gaps(df)
    report_gap_distribution(gaps)
    outages = detect_outages(df, gaps)
    summarise_outages(outages)

    daily = compute_daily_row_completeness(df)
    daily = add_daily_gap_stats(daily, outages)

    sensor_daily = compute_sensor_completeness(df)
    sensor_summary = summarise_sensor_completeness(df)

    daily_reliability = build_daily_reliability(daily, sensor_daily)
    summarise_daily_reliability(daily_reliability)

    written = [
        _write_csv(outages, output_dir, "outage_intervals.csv"),
        _write_csv(sensor_daily, output_dir, "sensor_completeness.csv"),
        _write_csv(daily_reliability, output_dir, "daily_reliability.csv"),
        plot_daily_completeness(daily_reliability, output_dir),
        plot_daily_largest_gap(daily_reliability, output_dir),
        plot_sensor_completeness(sensor_daily, output_dir),
        plot_gap_distribution(gaps, output_dir),
    ]

    print_summary(df, validation, daily, daily_reliability, sensor_summary, outages)

    print("=" * 78)
    print("OUTPUTS")
    print("=" * 78)
    for path in written:
        print(f"  {path}")
    print()


def _write_csv(frame, output_dir, filename):
    path = os.path.join(output_dir, filename)
    frame.to_csv(path, index=False)
    return path


if __name__ == "__main__":
    main()
