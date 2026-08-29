"""Tests for the shared health logic and the terminal renderer.

These use controlled inputs; no test contacts Google Sheets.
"""

import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import URLError
from unittest.mock import patch

import station_watch
from station_health import (
    HALIFAX,
    UTC,
    OperatingSchedule,
    SHEET_URL_VARIABLE,
    ConfigurationError,
    MonitorError,
    StationMonitor,
    Status,
    TelemetrySource,
    Thresholds,
    format_duration,
    format_timestamp,
    load_env_file,
    localize_wall_clock,
    sheet_url,
)


# Midday, inside the 06:00-23:00 powered window, so freshness rules apply.
NOW = datetime(2026, 8, 29, 12, 0, tzinfo=HALIFAX).astimezone(UTC)


class FakeSource(TelemetrySource):
    """A source that returns fixed timestamps, or raises a chosen error."""

    def __init__(self, timestamps=(), error=None):
        super().__init__()
        self.timestamps = list(timestamps)
        self.error = error

    def read_timestamps(self, reference=None):
        if self.error:
            raise self.error
        return sorted(self.timestamps)


def monitor_with(*ages_minutes, error=None, now=NOW):
    """A monitor whose readings arrived the given numbers of minutes ago."""
    timestamps = [now - timedelta(minutes=age) for age in ages_minutes]
    monitor = StationMonitor(source=FakeSource(timestamps, error))
    monitor.now = lambda: now
    return monitor


class ThresholdTests(unittest.TestCase):
    def test_classification_boundaries(self):
        thresholds = Thresholds()
        self.assertEqual(thresholds.classify(0), Status.HEALTHY)
        self.assertEqual(thresholds.classify(10), Status.HEALTHY)
        self.assertEqual(thresholds.classify(10.5), Status.DELAYED)
        self.assertEqual(thresholds.classify(29.9), Status.DELAYED)
        self.assertEqual(thresholds.classify(30), Status.OFFLINE)
        self.assertEqual(thresholds.classify(120), Status.OFFLINE)

    def test_thresholds_can_be_overridden_without_touching_logic(self):
        strict = Thresholds(healthy_max_minutes=5, offline_min_minutes=15)
        self.assertEqual(strict.classify(7), Status.DELAYED)


class HealthReportTests(unittest.TestCase):
    def test_healthy(self):
        report = monitor_with(20, 10, 4).check()
        self.assertEqual(report.status, Status.HEALTHY)
        self.assertEqual(report.age_text, "4m")
        self.assertEqual(report.summary, "Fresh telemetry is reaching Google Sheets.")

    def test_delayed(self):
        report = monitor_with(45, 16).check()
        self.assertEqual(report.status, Status.DELAYED)
        self.assertEqual(report.summary, "Telemetry is arriving later than expected.")

    def test_offline(self):
        report = monitor_with(120, 37).check()
        self.assertEqual(report.status, Status.OFFLINE)
        self.assertEqual(
            report.summary,
            "Fresh telemetry has not reached Google Sheets for 37m.",
        )

    def test_latest_timestamp_and_age_match_the_newest_reading(self):
        report = monitor_with(9, 3, 60).check()
        self.assertEqual(report.latest_timestamp, NOW - timedelta(minutes=3))
        self.assertAlmostEqual(report.age_seconds, 180)
        self.assertEqual(report.checked_at, NOW)

    def test_recent_gaps_are_inter_arrival_minutes(self):
        report = monitor_with(15, 10, 5).check()
        self.assertEqual([round(gap, 2) for _, gap in report.recent_gaps()], [5.0, 5.0])

    def test_future_timestamp_is_a_monitor_error(self):
        with self.assertRaises(MonitorError):
            monitor_with(-30).check()


class TimezoneTests(unittest.TestCase):
    def test_timestamps_are_read_as_halifax_local_and_carried_as_utc(self):
        source = TelemetrySource()
        parsed = source.parse_timestamps("Timestamp,Temperature\n2026-08-29 00:12:00,20\n")
        # Summer: ADT is UTC-3, so 00:12 local is 03:12 UTC.
        self.assertEqual(parsed[0], datetime(2026, 8, 29, 3, 12, tzinfo=UTC))
        self.assertEqual(format_timestamp(parsed[0]), "2026-08-29 00:12 ADT")

    def test_winter_timestamps_use_standard_time(self):
        source = TelemetrySource()
        parsed = source.parse_timestamps("Timestamp\n2026-01-15 08:00:00\n")
        # Winter: AST is UTC-4, so 08:00 local is 12:00 UTC.
        self.assertEqual(parsed[0], datetime(2026, 1, 15, 12, 0, tzinfo=UTC))
        self.assertEqual(format_timestamp(parsed[0]), "2026-01-15 08:00 AST")


class OperatingScheduleTests(unittest.TestCase):
    """Silence overnight is the schedule working, not the station failing."""

    def setUp(self):
        self.schedule = OperatingSchedule()

    def moment(self, hour, minute=0, day=29, month=8, year=2026):
        return datetime(year, month, day, hour, minute, tzinfo=HALIFAX)

    def test_powered_window_is_active_and_night_is_not(self):
        for hour in (6, 12, 22):
            self.assertFalse(self.schedule.is_inactive(self.moment(hour)), hour)
        for hour in (23, 0, 3, 5):
            self.assertTrue(self.schedule.is_inactive(self.moment(hour)), hour)

    def test_window_boundaries_are_half_open(self):
        # 06:00 is the first expected minute; 23:00 is the first silent one.
        self.assertFalse(self.schedule.is_inactive(self.moment(6, 0)))
        self.assertTrue(self.schedule.is_inactive(self.moment(5, 59)))
        self.assertFalse(self.schedule.is_inactive(self.moment(22, 59)))
        self.assertTrue(self.schedule.is_inactive(self.moment(23, 0)))

    def test_shutdown_does_not_apply_before_its_regime_began(self):
        # The building ran 24 h before 2026-04-21, so overnight silence then was
        # a real fault and must not be excused retroactively.
        before = self.moment(2, day=15, month=1)
        self.assertFalse(self.schedule.is_inactive(before))
        self.assertIn("continuous", self.schedule.describe(before))

    def test_regime_changeover_date_is_the_first_night_off(self):
        self.assertFalse(self.schedule.is_inactive(self.moment(2, day=20, month=4)))
        self.assertTrue(self.schedule.is_inactive(self.moment(2, day=21, month=4)))

    def test_resumes_at_points_to_the_next_window_open(self):
        self.assertEqual(
            self.schedule.resumes_at(self.moment(23, 30)), self.moment(6, day=30)
        )
        self.assertEqual(self.schedule.resumes_at(self.moment(3)), self.moment(6))
        self.assertIsNone(self.schedule.resumes_at(self.moment(12)))


class ScheduleAwareStatusTests(unittest.TestCase):
    def at(self, hour, minute, age_minutes, day=29):
        now = datetime(2026, 8, day, hour, minute, tzinfo=HALIFAX).astimezone(UTC)
        return monitor_with(age_minutes, now=now).check()

    def test_normal_thresholds_apply_inside_the_window(self):
        self.assertEqual(self.at(12, 0, 4).status, Status.HEALTHY)
        self.assertEqual(self.at(12, 0, 20).status, Status.DELAYED)
        self.assertEqual(self.at(12, 0, 90).status, Status.OFFLINE)

    def test_overnight_silence_is_scheduled_not_offline(self):
        report = self.at(2, 0, 180)
        self.assertEqual(report.status, Status.SCHEDULED_INACTIVE)
        # Three hours of silence would be OFFLINE at any other hour.
        self.assertEqual(Thresholds().classify(180), Status.OFFLINE)

    def test_scheduled_inactive_claims_nothing_about_the_station(self):
        summary = self.at(3, 0, 240).summary.lower()
        self.assertIn("not expected", summary)
        self.assertIn("unknown", summary)
        for claim in ("healthy", "fresh telemetry is reaching"):
            self.assertNotIn(claim, summary)

    def test_startup_grace_after_the_window_reopens(self):
        # Telemetry is hours old at 06:05 purely because the power was off.
        self.assertEqual(self.at(6, 5, 425).status, Status.HEALTHY)
        self.assertEqual(self.at(6, 20, 440).status, Status.HEALTHY)

    def test_grace_expires_and_escalates_normally(self):
        # Grace ends 06:15; the ordinary 10/30 minute limits run from there.
        self.assertEqual(self.at(6, 30, 450).status, Status.DELAYED)
        self.assertEqual(self.at(6, 50, 470).status, Status.OFFLINE)

    def test_fresh_telemetry_after_reopening_is_healthy_immediately(self):
        self.assertEqual(self.at(7, 0, 3).status, Status.HEALTHY)

    def test_overnight_silence_before_the_regime_is_still_offline(self):
        self.assertEqual(self.at(2, 0, 180, day=15).status, Status.SCHEDULED_INACTIVE)
        january = datetime(2026, 1, 15, 2, 0, tzinfo=HALIFAX).astimezone(UTC)
        self.assertEqual(monitor_with(180, now=january).check().status, Status.OFFLINE)

    def test_fresh_telemetry_overrides_the_inactive_window(self):
        # If data really is arriving at 02:00, the schedule is wrong and the
        # dashboard must not paper over it.
        self.assertEqual(self.at(2, 0, 4).status, Status.HEALTHY)

    def test_stale_telemetry_in_the_window_is_still_scheduled_inactive(self):
        self.assertEqual(self.at(2, 0, 45).status, Status.SCHEDULED_INACTIVE)

    def test_report_exposes_the_expected_window(self):
        self.assertIn("06:00-23:00", self.at(12, 0, 4).window_text)
        self.assertIsNone(self.at(12, 0, 4).resumes_at)
        self.assertIsNotNone(self.at(2, 0, 180).resumes_at)


class DaylightSavingTests(unittest.TestCase):
    """The Sheet stores local wall clock with no offset, so DST is ambiguous."""

    FALL_BACK = date(2025, 11, 2)     # 02:00 ADT -> 01:00 AST, 01:xx occurs twice
    SPRING_FORWARD = date(2026, 3, 8)  # 02:00 AST -> 03:00 ADT, 02:xx never occurs

    def test_ordinary_timestamps_are_unambiguous(self):
        moment, ambiguous = localize_wall_clock(datetime(2026, 8, 29, 12, 0), HALIFAX)
        self.assertFalse(ambiguous)
        self.assertEqual(moment, datetime(2026, 8, 29, 15, 0, tzinfo=UTC))
        winter, ambiguous = localize_wall_clock(datetime(2026, 1, 15, 8, 0), HALIFAX)
        self.assertFalse(ambiguous)
        self.assertEqual(winter, datetime(2026, 1, 15, 12, 0, tzinfo=UTC))

    def test_fall_back_hour_is_flagged_ambiguous(self):
        _, ambiguous = localize_wall_clock(datetime(2025, 11, 2, 1, 30), HALIFAX)
        self.assertTrue(ambiguous)

    def test_fall_back_resolves_against_the_first_pass(self):
        # 01:45 ADT: only the daylight-time reading has happened yet.
        now = datetime(2025, 11, 2, 1, 45, tzinfo=HALIFAX, fold=0).astimezone(UTC)
        moment, _ = localize_wall_clock(datetime(2025, 11, 2, 1, 30), HALIFAX, now)
        self.assertEqual(moment, datetime(2025, 11, 2, 4, 30, tzinfo=UTC))
        self.assertEqual((now - moment).total_seconds() / 60, 15)

    def test_fall_back_resolves_against_the_second_pass(self):
        # 01:45 AST, an hour later in real time. Naively attaching the zone would
        # pick the daylight reading and report the telemetry as 75 minutes old.
        now = datetime(2025, 11, 2, 1, 45, tzinfo=HALIFAX, fold=1).astimezone(UTC)
        moment, _ = localize_wall_clock(datetime(2025, 11, 2, 1, 30), HALIFAX, now)
        self.assertEqual(moment, datetime(2025, 11, 2, 5, 30, tzinfo=UTC))
        self.assertEqual((now - moment).total_seconds() / 60, 15)

        # What the old blind localisation did: 75 minutes, i.e. a false DELAYED.
        naive_guess = datetime(2025, 11, 2, 1, 30).replace(tzinfo=HALIFAX).astimezone(UTC)
        self.assertEqual((now - naive_guess).total_seconds() / 60, 75)

    def test_no_false_offline_across_the_fall_back(self):
        # A reading five real minutes old, taken during the second pass through
        # the repeated hour. Attaching the zone blindly would place it in the
        # first pass and call it 65 minutes old -- a false OFFLINE.
        now = datetime(2025, 11, 2, 1, 45, tzinfo=HALIFAX, fold=1).astimezone(UTC)
        source = TelemetrySource()
        stamps = source.parse_timestamps(
            "Timestamp\n2025-11-02 01:40:00\n", reference=now
        )
        monitor = StationMonitor(source=FakeSource(stamps))
        monitor.now = lambda: now
        report = monitor.check()
        # November 2025 predates the shutdown regime, so this is a live judgement.
        self.assertEqual(report.status, Status.HEALTHY)
        self.assertEqual(report.age_seconds / 60, 5)

        blind = datetime(2025, 11, 2, 1, 40).replace(tzinfo=HALIFAX).astimezone(UTC)
        self.assertEqual((now - blind).total_seconds() / 60, 65)
        self.assertEqual(Thresholds().classify(65), Status.OFFLINE)

    def test_spring_forward_gap_is_flagged_not_silently_accepted(self):
        # 02:30 never happens on this date; a timestamp claiming it is suspect.
        _, ambiguous = localize_wall_clock(datetime(2026, 3, 8, 2, 30), HALIFAX)
        self.assertTrue(ambiguous)

    def test_spring_forward_does_not_make_telemetry_look_fresher(self):
        now = datetime(2026, 3, 8, 3, 30, tzinfo=HALIFAX).astimezone(UTC)
        moment, _ = localize_wall_clock(datetime(2026, 3, 8, 1, 30), HALIFAX, now)
        # 01:30 AST to 03:30 ADT is one real hour, not two.
        self.assertEqual((now - moment).total_seconds() / 3600, 1)

    def test_ambiguity_is_surfaced_on_the_report(self):
        now = datetime(2025, 11, 2, 1, 45, tzinfo=HALIFAX, fold=1).astimezone(UTC)
        source = TelemetrySource()
        stamps = source.parse_timestamps(
            "Timestamp\n2025-11-02 01:30:00\n", reference=now
        )
        monitor = StationMonitor(source=source)
        source.read_timestamps = lambda reference=None: stamps
        monitor.now = lambda: now
        self.assertTrue(monitor.check().latest_is_ambiguous)


class ParsingTests(unittest.TestCase):
    def test_unparseable_rows_are_skipped_and_order_is_oldest_first(self):
        text = (
            "Timestamp,Temperature\n"
            "2026-08-29 11:55:00,21\n"
            "not a timestamp,22\n"
            "2026-08-29 11:50:00,20\n"
            ",\n"
        )
        parsed = TelemetrySource().parse_timestamps(text)
        self.assertEqual(
            parsed,
            [
                datetime(2026, 8, 29, 11, 50, tzinfo=HALIFAX).astimezone(UTC),
                datetime(2026, 8, 29, 11, 55, tzinfo=HALIFAX).astimezone(UTC),
            ],
        )

    def test_missing_timestamp_column_is_a_monitor_error(self):
        with self.assertRaises(MonitorError):
            TelemetrySource().parse_timestamps("Temperature\n20\n")

    def test_header_only_sheet_is_a_monitor_error_not_offline(self):
        with self.assertRaises(MonitorError) as raised:
            TelemetrySource().parse_timestamps("Timestamp,Temperature\n")
        # The source was retrieved, so the headline must not claim otherwise.
        self.assertIn("holds no", raised.exception.summary)


class ConfigurationTests(unittest.TestCase):
    """The telemetry URL comes from the environment and is never hard-coded."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.env_file = Path(self.directory.name) / ".env"
        # Keep every test independent of the developer's own environment.
        patcher = patch.dict(os.environ, {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_missing_variable_fails_gracefully(self):
        with patch("station_health.ENV_FILE", self.env_file):
            with self.assertRaises(ConfigurationError) as raised:
                sheet_url()
        self.assertIn(SHEET_URL_VARIABLE, raised.exception.summary)
        # A configuration problem is a monitor error, never a station status.
        self.assertIsInstance(raised.exception, MonitorError)

    def test_url_is_read_from_the_environment(self):
        os.environ[SHEET_URL_VARIABLE] = "https://example.invalid/export?format=csv"
        with patch("station_health.ENV_FILE", self.env_file):
            self.assertEqual(sheet_url(), "https://example.invalid/export?format=csv")

    def test_env_file_supplies_the_url_but_never_overrides_the_environment(self):
        self.env_file.write_text(
            "# comment\n\nSTATIONWATCH_SHEET_URL='https://example.invalid/from-file'\n",
            encoding="utf-8",
        )
        load_env_file(self.env_file)
        self.assertEqual(os.environ[SHEET_URL_VARIABLE], "https://example.invalid/from-file")

        os.environ[SHEET_URL_VARIABLE] = "https://example.invalid/exported"
        load_env_file(self.env_file)
        self.assertEqual(os.environ[SHEET_URL_VARIABLE], "https://example.invalid/exported")

    def test_source_construction_does_not_need_configuration(self):
        source = TelemetrySource()  # must not raise
        with patch("station_health.ENV_FILE", self.env_file):
            with self.assertRaises(ConfigurationError):
                source.download()

    def test_cli_reports_a_missing_setting_without_claiming_offline(self):
        with patch("station_health.ENV_FILE", self.env_file):
            with patch("builtins.print") as printed:
                exit_code = station_watch.main()
        output = " ".join(str(call.args[0]) for call in printed.call_args_list)
        self.assertEqual(exit_code, 1)
        self.assertIn("MONITOR ERROR", output)
        self.assertIn(SHEET_URL_VARIABLE, output)
        self.assertNotIn("OFFLINE", output)


class MonitorErrorTests(unittest.TestCase):
    def test_download_failure_raises_monitor_error(self):
        source = TelemetrySource(url="https://example.invalid/export?format=csv")
        with patch("station_health.urlopen", side_effect=URLError("network unavailable")):
            with self.assertRaises(MonitorError) as raised:
                source.download()
        self.assertEqual(
            raised.exception.summary, "StationWatch could not retrieve the telemetry source."
        )

    def test_cli_reports_monitor_error_and_never_offline(self):
        monitor = monitor_with(error=MonitorError("network unavailable"))
        with patch.object(station_watch, "StationMonitor", return_value=monitor):
            with patch("builtins.print") as printed:
                exit_code = station_watch.main()
        output = " ".join(str(call.args[0]) for call in printed.call_args_list)
        self.assertEqual(exit_code, 1)
        self.assertIn("MONITOR ERROR", output)
        self.assertIn("cannot be determined", output)
        self.assertNotIn("OFFLINE", output)
        self.assertNotIn("SCHEDULED INACTIVE", output)


class CliRenderingTests(unittest.TestCase):
    def test_report_lines(self):
        text = station_watch.render_report(monitor_with(4).check())
        self.assertIn("Status:          HEALTHY", text)
        self.assertIn("Last telemetry:  2026-08-29 11:56 ADT", text)
        self.assertIn("Age:             4m", text)

    def test_offline_output_makes_no_hardware_claim(self):
        text = station_watch.render_report(monitor_with(90).check()).lower()
        self.assertIn("google sheets", text)
        for claim in ("broken", "transmitter failed", "esp-now", "receiver failed"):
            self.assertNotIn(claim, text)


class DurationTests(unittest.TestCase):
    def test_readable_without_excess_precision(self):
        self.assertEqual(format_duration(41), "41s")
        self.assertEqual(format_duration(161.4), "2m 41s")
        self.assertEqual(format_duration(16 * 60), "16m")
        self.assertEqual(format_duration(97 * 60), "1h 37m")
        self.assertEqual(format_duration(-5), "0s")


if __name__ == "__main__":
    unittest.main()
