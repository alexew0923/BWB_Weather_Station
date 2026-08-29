"""Tests for the shared health logic and the terminal renderer.

These use controlled inputs; no test contacts Google Sheets.
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import URLError
from unittest.mock import patch

import station_watch
from station_health import (
    HALIFAX,
    SHEET_URL_VARIABLE,
    ConfigurationError,
    MonitorError,
    StationMonitor,
    Status,
    TelemetrySource,
    Thresholds,
    format_duration,
    load_env_file,
    sheet_url,
)


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=HALIFAX)


class FakeSource(TelemetrySource):
    """A source that returns fixed timestamps, or raises a chosen error."""

    def __init__(self, timestamps=(), error=None):
        super().__init__()
        self.timestamps = list(timestamps)
        self.error = error

    def read_timestamps(self):
        if self.error:
            raise self.error
        return sorted(self.timestamps)


def monitor_with(*ages_minutes, error=None):
    """A monitor whose readings arrived the given numbers of minutes ago."""
    timestamps = [NOW - timedelta(minutes=age) for age in ages_minutes]
    monitor = StationMonitor(source=FakeSource(timestamps, error))
    monitor.now = lambda: NOW
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
    def test_timestamps_are_parsed_as_halifax_local_time(self):
        source = TelemetrySource()
        parsed = source.parse_timestamps("Timestamp,Temperature\n2026-08-29 00:12:00,20\n")
        self.assertEqual(parsed[0].tzinfo, HALIFAX)
        self.assertEqual(parsed[0].strftime("%Z"), "ADT")
        self.assertEqual(parsed[0].utcoffset(), timedelta(hours=-3))

    def test_winter_timestamps_use_standard_time(self):
        source = TelemetrySource()
        parsed = source.parse_timestamps("Timestamp\n2026-01-15 08:00:00\n")
        self.assertEqual(parsed[0].strftime("%Z"), "AST")
        self.assertEqual(parsed[0].utcoffset(), timedelta(hours=-4))


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
                datetime(2026, 8, 29, 11, 50, tzinfo=HALIFAX),
                datetime(2026, 8, 29, 11, 55, tzinfo=HALIFAX),
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
