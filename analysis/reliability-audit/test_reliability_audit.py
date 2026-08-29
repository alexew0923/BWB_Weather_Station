"""Tests for the reliability audit's expected-count and timestamp handling.

Scope is deliberately narrow: the calculations whose failure would change a
reliability conclusion. Presentation code, figure generation and console
formatting are not tested -- a wrong plot is visible, a wrong denominator is not.
"""

import os
import unittest
from datetime import date, datetime, timedelta

import pandas as pd

from audit_config import (
    EXPECTED_TRANSMISSIONS_24H,
    EXPECTED_TRANSMISSIONS_POWERED,
    SENSOR_COLUMNS,
    STATION_TIMEZONE,
    active_window_for,
    expected_transmissions_for,
    regime_for,
    scheduled_transmissions_between,
)
from data_validation import localize_timestamps
from outage_analysis import classify_gap, compute_gaps, detect_outages, real_outages
from reliability_metrics import (
    add_slot_index,
    build_daily_reliability,
    compute_daily_row_completeness,
    compute_sensor_completeness,
    reconcile_transmissions,
)

# Resolved against this file, not the working directory, so the suite behaves the
# same from the repository root as from inside this package. The export itself is
# deliberately untracked (the root .gitignore excludes *.csv), so the one test
# that needs it skips cleanly on a fresh clone instead of erroring.
HISTORICAL_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "data", "HistoricalData.csv")
HAS_HISTORICAL_CSV = os.path.exists(HISTORICAL_CSV)


def local(text):
    """A Halifax-local timestamp from 'YYYY-MM-DD HH:MM:SS' text."""
    return pd.Timestamp(text, tz=STATION_TIMEZONE)


def frame(timestamps, **columns):
    """A minimal validated-style frame: localised timestamps plus sensor columns."""
    df = pd.DataFrame({"timestamp": pd.DatetimeIndex(timestamps)})
    for name in SENSOR_COLUMNS + ["Count"]:
        df[name] = columns.get(name, range(1, len(df) + 1))
    return df.sort_values("timestamp").reset_index(drop=True)


def every_five_minutes(start, count, step_minutes=5):
    first = local(start)
    return [first + timedelta(minutes=step_minutes * i) for i in range(count)]


class ExpectedTransmissionTests(unittest.TestCase):
    """The denominator behind every completeness figure in the audit."""

    def test_continuous_regime_expects_288_a_day(self):
        self.assertEqual(expected_transmissions_for(date(2026, 3, 1)),
                         EXPECTED_TRANSMISSIONS_24H)

    def test_powered_regime_expects_204_a_day(self):
        self.assertEqual(expected_transmissions_for(date(2026, 5, 1)),
                         EXPECTED_TRANSMISSIONS_POWERED)

    def test_regime_changes_exactly_on_the_changeover_date(self):
        self.assertEqual(expected_transmissions_for(date(2026, 4, 20)), 288)
        self.assertEqual(expected_transmissions_for(date(2026, 4, 21)), 204)
        self.assertEqual(regime_for(date(2026, 4, 20)).active_end_hour, 24)
        self.assertEqual(regime_for(date(2026, 4, 21)).active_end_hour, 23)

    def test_daylight_saving_days_are_not_288(self):
        # A 25-hour fall-back day can schedule 300 transmissions and a 23-hour
        # spring-forward day only 276. Measuring either against a flat 288 makes
        # a normal day look over- or under-performing.
        self.assertEqual(expected_transmissions_for(date(2025, 11, 2)), 300)
        self.assertEqual(expected_transmissions_for(date(2026, 3, 8)), 276)

    def test_dst_does_not_move_the_powered_window(self):
        # The 02:00 transition sits outside 06:00-23:00, so nothing shifts.
        self.assertEqual(expected_transmissions_for(date(2026, 11, 1)), 204)


class ScheduledSlotTests(unittest.TestCase):
    """Slots are counted against powered minutes, never wall-clock minutes."""

    def test_nominal_gap_is_one_slot(self):
        self.assertEqual(
            scheduled_transmissions_between(
                local("2026-05-01 12:00:00"), local("2026-05-01 12:05:00")), 1)

    def test_ten_minute_gap_is_two_slots(self):
        self.assertEqual(
            scheduled_transmissions_between(
                local("2026-05-01 12:00:00"), local("2026-05-01 12:10:00")), 2)

    def test_overnight_shutdown_contains_almost_no_slots(self):
        # 22:57 -> 06:01 is 424 wall-clock minutes but only ~4 powered ones, so
        # the old "gap minutes / 5" formula invented 84 lost transmissions here.
        slots = scheduled_transmissions_between(
            local("2026-05-01 22:57:00"), local("2026-05-02 06:01:00"))
        self.assertLessEqual(slots, 1)
        self.assertEqual(round(424 / 5) - 1, 84)

    def test_multi_day_outage_counts_only_powered_time(self):
        # Sixteen days under the 204/day regime, not 16 * 288.
        start, end = local("2026-04-22 09:16:00"), local("2026-05-08 09:16:00")
        self.assertAlmostEqual(
            scheduled_transmissions_between(start, end), 16 * 204, delta=2)

    def test_slots_before_the_changeover_use_the_continuous_regime(self):
        start, end = local("2026-01-01 00:00:00"), local("2026-01-02 00:00:00")
        self.assertEqual(scheduled_transmissions_between(start, end), 288)


class ReconciliationTests(unittest.TestCase):
    """received + missed - surplus must equal expected, with no residual."""

    def reconcile(self, timestamps):
        df = add_slot_index(frame(timestamps))
        daily = compute_daily_row_completeness(df)
        return reconcile_transmissions(df, daily)

    def test_a_perfect_day_leaves_nothing_missing(self):
        result = self.reconcile(every_five_minutes("2026-05-01 06:00:00", 204))
        self.assertEqual(result["expected"], 204)
        self.assertEqual(result["received"], 204)
        self.assertEqual(result["missed"], 0)
        self.assertEqual(result["residual"], 0)

    def test_a_partial_day_accounts_for_the_shortfall(self):
        result = self.reconcile(every_five_minutes("2026-05-01 06:00:00", 100))
        self.assertEqual(result["expected"], 204)
        self.assertEqual(result["received"], 100)
        self.assertEqual(result["missed"], 104)
        self.assertEqual(result["residual"], 0)

    def test_identity_holds_across_the_nightly_shutdown(self):
        # Two full powered days with the shutdown between them.
        stamps = (every_five_minutes("2026-05-01 06:00:00", 204)
                  + every_five_minutes("2026-05-02 06:00:00", 204))
        result = self.reconcile(stamps)
        self.assertEqual(result["expected"], 408)
        self.assertEqual(result["missed"], 0)
        self.assertEqual(result["residual"], 0)

    def test_identity_holds_across_the_regime_changeover(self):
        stamps = (every_five_minutes("2026-04-20 00:00:00", 288)
                  + every_five_minutes("2026-04-21 06:00:00", 204))
        result = self.reconcile(stamps)
        self.assertEqual(result["expected"], 288 + 204)
        self.assertEqual(result["residual"], 0)

    def test_duplicate_rows_become_surplus_not_negative_loss(self):
        stamps = every_five_minutes("2026-05-01 06:00:00", 204)
        stamps += [stamps[10] + timedelta(seconds=20)]  # a sub-minute repeat
        result = self.reconcile(stamps)
        self.assertEqual(result["received"], 205)
        self.assertEqual(result["surplus"], 1)
        self.assertEqual(result["residual"], 0)

    @unittest.skipUnless(HAS_HISTORICAL_CSV,
                         "data/HistoricalData.csv is not present (it is untracked)")
    def test_identity_holds_on_the_real_dataset(self):
        # The regression this whole change exists for: the shipped audit
        # published 24,833 received + 48,828 missed against 69,852 expected,
        # overshooting by 3,811 transmissions (5.5%).
        from data_validation import load_and_validate_data
        import contextlib
        import io

        with contextlib.redirect_stdout(io.StringIO()):
            df, _ = load_and_validate_data(HISTORICAL_CSV)
        df = add_slot_index(df)
        daily = compute_daily_row_completeness(df)
        result = reconcile_transmissions(df, daily)

        self.assertEqual(result["residual"], 0)
        self.assertEqual(
            result["received"] + result["missed"] - result["surplus"],
            result["expected"],
        )


class DaylightSavingTests(unittest.TestCase):
    """The raw column is local wall clock, so it is not a monotonic timeline."""

    def localize(self, texts):
        naive = pd.Series(pd.to_datetime(pd.Series(texts)))
        log = []
        return localize_timestamps(naive, log), log

    def test_ordinary_timestamps_are_unchanged(self):
        aware, log = self.localize(["2026-05-01 12:00:00", "2026-05-01 12:05:00"])
        self.assertEqual((aware.iloc[1] - aware.iloc[0]).total_seconds(), 300)
        self.assertIn("no DST transition timestamps", "\n".join(log))

    def test_fall_back_repeat_is_resolved_from_file_order(self):
        # The real 2025-11-02 sequence: the wall clock goes backwards, but the
        # station kept its five-minute cadence throughout.
        aware, _ = self.localize([
            "2025-11-02 01:53:44",
            "2025-11-02 01:58:38",
            "2025-11-02 01:03:33",   # second pass through the repeated hour
            "2025-11-02 01:08:28",
        ])
        gaps = aware.diff().dt.total_seconds() / 60
        self.assertTrue((gaps.dropna() > 0).all(), "no gap may run backwards")
        for gap in gaps.dropna():
            self.assertAlmostEqual(gap, 4.9, delta=0.2)

    def test_fall_back_would_look_like_a_backward_jump_when_naive(self):
        naive = pd.to_datetime(pd.Series(
            ["2025-11-02 01:58:38", "2025-11-02 01:03:33"]))
        self.assertLess(naive.diff().iloc[1].total_seconds(), 0)

    def test_fall_back_rows_are_reported_not_silently_resolved(self):
        _, log = self.localize([
            "2025-11-02 01:53:44", "2025-11-02 01:03:33"])
        self.assertIn("DST fall-back", "\n".join(log))

    def test_spring_forward_gap_is_one_real_hour(self):
        # 01:55 AST to 03:00 ADT is five minutes, not sixty-five.
        aware, _ = self.localize(["2026-03-08 01:55:00", "2026-03-08 03:00:00"])
        self.assertAlmostEqual(
            (aware.iloc[1] - aware.iloc[0]).total_seconds() / 60, 5, delta=0.1)

    def test_localized_timeline_is_monotonic(self):
        aware, _ = self.localize([
            "2025-11-02 01:48:48", "2025-11-02 01:53:44", "2025-11-02 01:58:38",
            "2025-11-02 01:03:33", "2025-11-02 01:08:28", "2025-11-02 01:13:23",
        ])
        self.assertTrue(aware.is_monotonic_increasing)


class GapClassificationTests(unittest.TestCase):
    def test_scheduled_shutdown_is_not_an_outage(self):
        start, end = local("2026-05-01 22:57:00"), local("2026-05-02 06:01:00")
        self.assertEqual(
            classify_gap(424.0, start, end), "scheduled overnight shutdown")

    def test_overnight_gap_costs_no_missed_transmissions(self):
        stamps = (every_five_minutes("2026-05-01 22:47:00", 3)
                  + every_five_minutes("2026-05-02 06:00:00", 3))
        df = add_slot_index(frame(stamps))
        outages = detect_outages(df, compute_gaps(df))
        overnight = outages[outages["severity"] == "scheduled overnight shutdown"]
        self.assertEqual(len(overnight), 1)
        self.assertEqual(int(overnight["missed_transmissions"].iloc[0]), 0)

    def test_daytime_outage_counts_its_missed_transmissions(self):
        stamps = (every_five_minutes("2026-05-01 09:00:00", 3)
                  + every_five_minutes("2026-05-01 11:00:00", 3))
        df = add_slot_index(frame(stamps))
        outages = real_outages(detect_outages(df, compute_gaps(df)))
        self.assertEqual(len(outages), 1)
        # 09:10 -> 11:00 is 110 minutes of powered time: 22 slots, 21 lost.
        self.assertEqual(int(outages["missed_transmissions"].iloc[0]), 21)


class DayClassificationTests(unittest.TestCase):
    def classify(self, timestamps):
        df = add_slot_index(frame(timestamps))
        daily = compute_daily_row_completeness(df)
        daily["largest_gap_minutes"] = 0.0
        return build_daily_reliability(daily, compute_sensor_completeness(df))

    def test_zero_row_day_between_two_good_days_is_a_full_outage(self):
        stamps = (every_five_minutes("2026-05-01 06:00:00", 204)
                  + every_five_minutes("2026-05-03 06:00:00", 204))
        classified = self.classify(stamps)
        by_date = dict(zip(classified["date"], classified["failure_class"]))
        self.assertEqual(by_date[date(2026, 5, 2)], "Full outage")
        self.assertEqual(by_date[date(2026, 5, 1)], "Good day")

    def test_partial_day_is_partial_transmission_loss(self):
        classified = self.classify(every_five_minutes("2026-05-01 06:00:00", 100))
        self.assertEqual(classified["failure_class"].iloc[0],
                         "Partial transmission loss")

    def test_severe_day_is_severe_transmission_loss(self):
        classified = self.classify(every_five_minutes("2026-05-01 06:00:00", 20))
        self.assertEqual(classified["failure_class"].iloc[0],
                         "Severe transmission loss")

    def test_fast_cycling_day_is_not_reported_as_perfect(self):
        # More rows than the schedule physically allows. The deprecated in-sheet
        # audit capped completeness at 100% and called this a clean day.
        classified = self.classify(
            every_five_minutes("2026-05-01 06:00:00", 400, step_minutes=2))
        self.assertEqual(classified["failure_class"].iloc[0],
                         "Over-baseline (fast-cycling / repeat transmissions)")
        self.assertGreater(classified["row_completeness_raw"].iloc[0], 1.0)

    def test_midnight_jitter_is_not_called_fast_cycling(self):
        # A handful of extra rows either side of midnight is not a malfunction.
        classified = self.classify(every_five_minutes("2026-05-01 06:00:00", 208))
        self.assertNotIn("Over-baseline", classified["failure_class"].iloc[0])


if __name__ == "__main__":
    unittest.main()
