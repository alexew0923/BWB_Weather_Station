"""Constants and fixed assumptions for the reliability audit."""

from datetime import date

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

