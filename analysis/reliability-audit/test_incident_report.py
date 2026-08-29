"""Focused tests for the CLI Incident Explorer's reliability conclusions."""

import unittest
from datetime import timedelta

import numpy as np
import pandas as pd

from audit_config import SENSOR_COLUMNS, STATION_TIMEZONE
from incident_analysis import analyze_incident, parse_incident_timestamp, select_detected_outage


def local(text):
    return pd.Timestamp(text, tz=STATION_TIMEZONE)


def frame(timestamps, battery=3800.0, missing_sensor=None):
    rows = pd.DataFrame({"timestamp": pd.DatetimeIndex(timestamps)})
    for column in SENSOR_COLUMNS:
        rows[column] = battery if column == "Battery Voltage" else 10.0
    if missing_sensor:
        rows[missing_sensor] = np.nan
    rows["Count"] = range(1, len(rows) + 1)
    return rows.sort_values("timestamp").reset_index(drop=True)


def cadence(start, count, minutes=5):
    first = local(start)
    return [first + timedelta(minutes=minutes * index) for index in range(count)]


class IncidentAnalysisTests(unittest.TestCase):
    def test_complete_outage_interval(self):
        df = frame(cadence("2026-05-01 08:00", 12) + cadence("2026-05-01 10:00", 12))
        report = analyze_incident(df, local("2026-05-01 09:00"), local("2026-05-01 10:00"))
        self.assertEqual(report["incident"]["received_readings"], 0)
        self.assertEqual(report["incident"]["expected_readings"], 12)
        self.assertIn("No telemetry rows", report["interpretation"]["observed"][0])

    def test_partial_loss_interval(self):
        stamps = cadence("2026-05-01 08:00", 12) + [
            local("2026-05-01 09:05"), local("2026-05-01 09:25"), local("2026-05-01 09:55")
        ] + cadence("2026-05-01 10:00", 12)
        report = analyze_incident(frame(stamps), local("2026-05-01 09:00"), local("2026-05-01 10:00"))
        self.assertEqual(report["incident"]["received_readings"], 3)
        self.assertAlmostEqual(report["incident"]["telemetry_completeness"], 0.25)

    def test_scheduled_inactive_overlap(self):
        report = analyze_incident(
            frame([local("2026-05-01 21:55"), local("2026-05-02 07:00")]),
            local("2026-05-01 22:00"), local("2026-05-02 07:00"),
        )
        self.assertTrue(report["incident"]["overlaps_scheduled_inactive_period"])
        self.assertAlmostEqual(report["incident"]["scheduled_inactive_hours"], 7.0)
        self.assertEqual(report["incident"]["expected_readings"], 24)

    def test_continuous_operation_regime(self):
        report = analyze_incident(
            frame([local("2026-01-01 00:00")]),
            local("2026-01-01 00:00"), local("2026-01-02 00:00"),
        )
        self.assertFalse(report["incident"]["overlaps_scheduled_inactive_period"])
        self.assertEqual(report["incident"]["expected_readings"], 288)

    def test_no_battery_data_reports_unavailable(self):
        df = frame(cadence("2026-05-01 08:00", 36), battery=np.nan)
        report = analyze_incident(df, local("2026-05-01 09:00"), local("2026-05-01 10:00"))
        self.assertEqual(report["pre_window"]["battery"]["status"], "not commissioned by this window")
        self.assertIsNone(report["pre_window"]["battery"]["trend_volts_per_hour"])

    def test_battery_trend_is_descriptive(self):
        df = frame(cadence("2026-05-01 08:00", 36))
        df["Battery Voltage"] = np.linspace(3810, 3670, len(df))
        report = analyze_incident(df, local("2026-05-01 09:00"), local("2026-05-01 10:00"))
        self.assertLess(report["pre_window"]["battery"]["trend_volts_per_hour"], 0)
        self.assertIn("does not establish causation", report["interpretation"]["suggestive"][0])

    def test_no_recovery_within_post_window(self):
        df = frame(cadence("2026-05-01 08:00", 12) + [local("2026-05-02 00:00")])
        report = analyze_incident(
            df, local("2026-05-01 09:00"), local("2026-05-01 10:00"), after_hours=2
        )
        self.assertFalse(report["post_window"]["recovery_within_selected_window"])
        self.assertIsNone(report["post_window"]["recovery_delay_hours"])

    def test_end_must_follow_start(self):
        df = frame([local("2026-05-01 09:00")])
        with self.assertRaisesRegex(ValueError, "end must be after"):
            analyze_incident(df, local("2026-05-01 10:00"), local("2026-05-01 10:00"))

    def test_dst_sensitive_interval_uses_elapsed_utc_time(self):
        start = parse_incident_timestamp("2026-03-08 01:30")
        end = parse_incident_timestamp("2026-03-08 03:30")
        report = analyze_incident(frame([start]), start, end)
        self.assertEqual(report["incident"]["duration_hours"], 1.0)
        self.assertEqual(report["incident"]["expected_readings"], 12)

    def test_ambiguous_local_cli_time_requires_offset(self):
        with self.assertRaisesRegex(ValueError, "explicit UTC offset"):
            parse_incident_timestamp("2025-11-02 01:30")
        explicit = parse_incident_timestamp("2025-11-02 01:30-03:00")
        self.assertIsNotNone(explicit.tzinfo)

    def test_interpretation_does_not_claim_component_root_cause(self):
        df = frame(cadence("2026-05-01 08:00", 12) + cadence("2026-05-01 10:00", 12))
        report = analyze_incident(df, local("2026-05-01 09:00"), local("2026-05-01 10:00"))
        text = " ".join(sum(report["interpretation"].values(), [])).lower()
        self.assertNotIn("caused the outage", text)
        self.assertIn("cannot distinguish", text)
        self.assertIn("not determinable", text)

    def test_detected_outage_selection_reuses_existing_table(self):
        df = frame(cadence("2026-05-01 08:00", 3) + cadence("2026-05-01 10:00", 3))
        start, end, metadata, count = select_detected_outage(df, 1)
        self.assertEqual(count, 1)
        self.assertEqual(start, local("2026-05-01 08:10"))
        self.assertEqual(end, local("2026-05-01 10:00"))
        self.assertEqual(metadata["severity"], "moderate")
        report = analyze_incident(df, start, end, detected_outage=metadata)
        self.assertEqual(report["incident"]["expected_readings"], 21)
        self.assertEqual(report["incident"]["received_readings"], 0)


if __name__ == "__main__":
    unittest.main()
