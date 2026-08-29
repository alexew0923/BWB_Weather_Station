"""Focused synthetic tests for sensor-health evidence calculations."""

import unittest
from datetime import timedelta

import numpy as np
import pandas as pd

from sensor_analysis import (
    analyze_sensor_health,
    compute_rate_table,
    detect_cross_sensor_events,
    detect_flatlines,
    detect_missing_runs,
    detect_rate_events,
    detect_value_bound_events,
    elapsed_minutes,
    prepare_sensor_frame,
    soil_expected_opportunity_mask,
)
from sensor_rules import ALL_SENSORS


TZ = "America/Halifax"


def frame(times, **overrides):
    count = len(times)
    data = {
        "timestamp": pd.DatetimeIndex(times),
        "Temperature": np.linspace(10, 11, count),
        "Humidity": np.linspace(60, 61, count),
        "Soil Moisture": np.linspace(1500, 1510, count),
        "Air Pressure": np.linspace(1000, 1001, count),
        "Rain Value": np.linspace(3900, 3910, count),
        "Battery Voltage": np.linspace(3800, 3810, count),
        "Count": np.arange(1, count + 1, dtype=float),
    }
    data.update(overrides)
    return pd.DataFrame(data)


def times(start="2026-07-01 12:00", count=12, minutes=5):
    first = pd.Timestamp(start, tz=TZ)
    return [first + timedelta(minutes=minutes * index) for index in range(count)]


class MissingnessTests(unittest.TestCase):
    def test_sensor_specific_missingness_is_an_event(self):
        df = frame(times(count=3), **{"Air Pressure": [1000.0, np.nan, 1001.0]})
        events = detect_missing_runs(df)
        pressure = [event for event in events if event["sensor"] == "Air Pressure"]
        self.assertEqual(len(pressure), 1)
        self.assertEqual(pressure[0]["sample_count"], 1)

    def test_absent_telemetry_row_is_not_a_sensor_failure(self):
        # The missing 12:05 row does not exist in the received-row frame.
        df = frame([pd.Timestamp("2026-07-01 12:00", tz=TZ),
                    pd.Timestamp("2026-07-01 12:10", tz=TZ)])
        self.assertEqual(detect_missing_runs(df), [])

    def test_missing_events_do_not_bridge_a_telemetry_outage(self):
        df = frame([
            pd.Timestamp("2026-07-01 12:00", tz=TZ),
            pd.Timestamp("2026-07-01 14:00", tz=TZ),
        ], **{"Air Pressure": [np.nan, np.nan]})
        pressure = [event for event in detect_missing_runs(df)
                    if event["sensor"] == "Air Pressure"]
        self.assertEqual(len(pressure), 2)
        self.assertTrue(all(event["duration_minutes"] == 0 for event in pressure))

    def test_missing_event_grouping_uses_received_row_adjacency(self):
        df = frame(times(count=4), **{"Air Pressure": [1000, np.nan, np.nan, 1001]})
        pressure = [event for event in detect_missing_runs(df)
                    if event["sensor"] == "Air Pressure"]
        self.assertEqual(len(pressure), 1)
        self.assertEqual(pressure[0]["sample_count"], 2)
        self.assertEqual(pressure[0]["duration_minutes"], 5)


class ValueAndDynamicsTests(unittest.TestCase):
    def test_impossible_pressure_is_reported_not_removed(self):
        df = frame(times(count=3), **{"Air Pressure": [1000.0, 4.04, 1001.0]})
        events = detect_value_bound_events(df)
        impossible = [event for event in events if event["event_type"] == "impossible_reading"]
        self.assertEqual(len(impossible), 1)
        self.assertIn("4.04", impossible[0]["observed_value"])
        self.assertEqual(df["Air Pressure"].iloc[1], 4.04)

    def test_flatline_requires_duration_and_samples(self):
        df = frame(times(count=12), **{"Temperature": np.full(12, 10.0)})
        events = detect_flatlines(df)
        temperature = [event for event in events if event["sensor"] == "Temperature"]
        self.assertEqual(len(temperature), 1)
        self.assertEqual(temperature[0]["duration_minutes"], 55)

    def test_humidity_saturation_flatline_is_minor_not_failure_claim(self):
        df = frame(times(count=12), **{"Humidity": np.full(12, 100.0)})
        event = [item for item in detect_flatlines(df) if item["sensor"] == "Humidity"][0]
        self.assertEqual(event["severity"], "minor")
        self.assertIn("not classified as failure", event["context"])

    def test_irregular_rate_uses_actual_elapsed_time(self):
        df = frame([
            pd.Timestamp("2026-07-01 12:00", tz=TZ),
            pd.Timestamp("2026-07-01 12:07", tz=TZ),
        ], **{"Temperature": [10.0, 17.0]})
        rates = compute_rate_table(df)
        rate = rates[rates["sensor"] == "Temperature"].iloc[0]
        self.assertEqual(rate["elapsed_minutes"], 7)
        self.assertAlmostEqual(rate["rate_per_minute"], 1.0)

    def test_pressure_change_can_be_physical_rule_event(self):
        df = frame(times(count=3), **{"Air Pressure": [1000.0, 4.04, 1000.0]})
        events, _ = detect_rate_events(compute_rate_table(df))
        self.assertTrue(any(event["event_type"] == "physically_implausible_change"
                            for event in events))


class TimeHandlingTests(unittest.TestCase):
    def test_elapsed_minutes_is_dst_safe(self):
        first = pd.Timestamp("2025-11-02 01:55:00-03:00")
        second = pd.Timestamp("2025-11-02 01:05:00-04:00")
        self.assertEqual(elapsed_minutes(first, second), 10)

    def test_naive_timestamps_are_localized(self):
        df = frame(pd.date_range("2026-07-01 12:00", periods=2, freq="5min"))
        prepared, _ = prepare_sensor_frame(df)
        self.assertEqual(str(prepared["timestamp"].dt.tz), TZ)


class SemanticsAndIntegrityTests(unittest.TestCase):
    def test_soil_periodic_sampling_opportunities(self):
        df = frame(times(start="2026-05-01 12:00", count=7),
                   **{"Count": np.arange(1, 8, dtype=float),
                      "Temperature": np.full(7, 10.0),
                      "Soil Moisture": [np.nan] * 7})
        expected = soil_expected_opportunity_mask(df)
        self.assertEqual(expected.tolist(), [False, False, False, False, False, True, False])
        soil = [event for event in detect_missing_runs(df) if event["sensor"] == "Soil Moisture"]
        self.assertEqual(len(soil), 1)
        self.assertEqual(soil[0]["sample_count"], 1)

    def test_temperature_humidity_signature_is_module_level_pattern(self):
        df = frame(times(count=2), Temperature=[np.nan, 11], Humidity=[np.nan, 61])
        events = detect_cross_sensor_events(df)
        self.assertEqual(len(events), 1)
        self.assertIn("sensor-module-level pattern", events[0]["context"])

    def test_malformed_sensor_cell_is_coerced_and_counted(self):
        df = frame(times(count=2), Humidity=["bad", "60.0"])
        prepared, report = prepare_sensor_frame(df)
        self.assertTrue(pd.isna(prepared["Humidity"].iloc[0]))
        self.assertEqual(report["malformed_numeric_cells"]["Humidity"], 1)

    def test_malformed_timestamp_row_is_dropped(self):
        df = frame(times(count=2))
        df["timestamp"] = ["not-a-date", "2026-07-01 12:05:00"]
        prepared, report = prepare_sensor_frame(df)
        self.assertEqual(len(prepared), 1)
        self.assertEqual(report["malformed_timestamps_dropped"], 1)

    def test_empty_dataset_fails_clearly(self):
        df = pd.DataFrame(columns=["timestamp", *ALL_SENSORS, "Count"])
        with self.assertRaisesRegex(ValueError, "no usable rows"):
            prepare_sensor_frame(df)

    def test_missing_column_fails_clearly(self):
        df = frame(times(count=2)).drop(columns=["Air Pressure"])
        with self.assertRaisesRegex(ValueError, "Air Pressure"):
            prepare_sensor_frame(df)

    def test_complete_analysis_returns_expected_tables(self):
        results = analyze_sensor_health(frame(times(count=12)))
        for name in ("daily", "regimes", "events", "pressure_clusters", "sensor_summary"):
            self.assertIn(name, results)


if __name__ == "__main__":
    unittest.main()
