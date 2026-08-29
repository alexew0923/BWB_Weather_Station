"""CSV loading and integrity validation for the reliability audit."""

import os

import numpy as np
import pandas as pd

from audit_config import (
    MAX_PLAUSIBLE_COUNT,
    PLAUSIBLE_SENSOR_RANGES,
    SENSOR_COLUMNS,
    STATION_TIMEZONE,
    TIMESTAMP_COLUMN_CANDIDATES,
)

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


def _ambiguous_mask(naive):
    """Rows whose local wall-clock time occurs twice on a DST fall-back day."""
    # ambiguous="NaT" nulls both ambiguous and non-existent times; ambiguous=True
    # nulls only the non-existent ones, so the difference isolates the ambiguous.
    either = naive.dt.tz_localize(STATION_TIMEZONE, ambiguous="NaT", nonexistent="NaT")
    nonexistent_only = naive.dt.tz_localize(STATION_TIMEZONE, ambiguous=True, nonexistent="NaT")
    return either.isna() & nonexistent_only.notna()


def _resolve_fall_back(naive, ambiguous):
    """
    Decide, per ambiguous row, whether it belongs to the first or second pass
    through the repeated hour.

    Resolved from FILE ORDER rather than guessed. Rows reach the sheet in
    arrival order, so within one repeated hour the first pass (Atlantic Daylight
    Time) is written before the second (Atlantic Standard Time). The switch is
    the point where the wall clock jumps backwards -- on the shipped dataset
    that is 2025-11-02 01:58:38 -> 01:03:33, with Count still monotonic across
    it, which is what confirms the clock moved and the transmitter did not.

    Returns a boolean Series in pandas' convention: True = first/DST pass.
    """
    choice = pd.Series(True, index=naive.index)
    if not ambiguous.any():
        return choice

    # One repeated hour per transition, so group by the local calendar date.
    for _, positions in naive[ambiguous].groupby(naive[ambiguous].dt.date).groups.items():
        ordered = sorted(positions)
        second_pass = False
        previous = None
        for position in ordered:
            moment = naive.loc[position]
            if previous is not None and moment < previous:
                second_pass = True
            if second_pass:
                choice.loc[position] = False
            previous = moment
    return choice


def localize_timestamps(naive, log):
    """
    Attach America/Halifax to naive wall-clock timestamps, resolving DST.

    The sheet stores Apps Script receipt time as local text with no UTC offset,
    so the raw column is not a monotonic timeline: an Atlantic fall-back repeats
    the 01:00 hour and a spring-forward skips the 02:00 hour. Localising makes
    every later duration real elapsed time, which is what stops the fall-back
    from reading as a 55-minute backward jump and the spring-forward from
    reading as a phantom 60-minute outage.

    Ambiguous rows are resolved from file order (see _resolve_fall_back); any
    that cannot be are left on the first pass and reported, never silently
    dropped.
    """
    ambiguous = _ambiguous_mask(naive)
    nonexistent = naive.dt.tz_localize(
        STATION_TIMEZONE, ambiguous=True, nonexistent="NaT"
    ).isna()

    choice = _resolve_fall_back(naive, ambiguous)
    aware = naive.dt.tz_localize(
        STATION_TIMEZONE, ambiguous=choice.to_numpy(), nonexistent="shift_forward"
    )

    log.append(f"Timezone applied       : {STATION_TIMEZONE.key} (local wall clock, no offset in source)")
    if ambiguous.any():
        second = int((~choice[ambiguous]).sum())
        log.append(
            f"    DST fall-back: {int(ambiguous.sum())} ambiguous timestamp(s) resolved from "
            f"file order ({int(ambiguous.sum()) - second} first pass / {second} second pass)"
        )
        for position in naive.index[ambiguous][:4]:
            log.append(f"        {naive.loc[position]} -> {aware.loc[position]}")
        if int(ambiguous.sum()) > 4:
            log.append(f"        ... and {int(ambiguous.sum()) - 4} more")
    if nonexistent.any():
        log.append(
            f"    DST spring-forward: {int(nonexistent.sum())} non-existent timestamp(s) "
            f"shifted forward out of the skipped hour"
        )
    if not ambiguous.any() and not nonexistent.any():
        log.append("    no DST transition timestamps in this dataset")
    return aware


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

    Runs on LOCALISED timestamps, so the annual DST fall-back -- which is a
    backward jump on the raw wall clock but not in real time -- is already
    resolved and does not appear here. Anything left is a genuine clock anomaly.
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
    # Localise BEFORE the backward-jump check: on a naive series the annual
    # fall-back looks like a device fault, and it is not one.
    raw["timestamp"] = localize_timestamps(raw["timestamp"], log)
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

