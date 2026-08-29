"""Focused tests for standalone battery analytics and the energy model."""

import json
import math
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from battery_analysis import (
    MIN_TREND_SAMPLES,
    STATION_TIMEZONE,
    analyze_relationships,
    battery_data_quality,
    compute_daily_battery_metrics,
    compute_outage_battery_context,
    compute_rolling_battery_metrics,
    fit_voltage_slope,
    load_reliability_exports,
    reliability_exports_match_data,
    run_battery_analysis,
)
from energy_model import EnergyModelParameters, model_daily_power_budget


def local(text):
    return pd.Timestamp(text, tz=STATION_TIMEZONE)


def frame(timestamps, voltages, temperatures=None):
    count = len(timestamps)
    if temperatures is None:
        temperatures = np.linspace(10, 20, count)
    return pd.DataFrame({
        "timestamp": pd.DatetimeIndex(timestamps),
        "Battery Voltage": voltages,
        "Temperature": temperatures,
        "Humidity": np.full(count, 50.0),
        "Rain Value": np.full(count, 2000.0),
    }).sort_values("timestamp").reset_index(drop=True)


def cadence(start, count, minutes=5):
    first = local(start)
    return [first + timedelta(minutes=minutes * index) for index in range(count)]


def reliability_daily(days, completeness=1.0):
    return pd.DataFrame({
        "date": days,
        "row_completeness": np.full(len(days), completeness),
        "largest_gap_minutes": np.zeros(len(days)),
    })


def empty_outages():
    return pd.DataFrame(columns=[
        "gap_start", "gap_end", "gap_minutes", "gap_hours", "severity",
        "missed_transmissions", "count_before", "count_after",
    ])


def one_outage(start="2026-05-02 12:00", end="2026-05-02 14:00"):
    return pd.DataFrame([{
        "gap_start": local(start), "gap_end": local(end),
        "gap_minutes": 120.0, "gap_hours": 2.0, "severity": "moderate",
        "missed_transmissions": 23, "count_before": 1, "count_after": 2,
    }])


class BatteryQualityTests(unittest.TestCase):
    def test_battery_unavailable_before_commissioning(self):
        df = frame(cadence("2026-03-01 00:00", 12), np.full(12, np.nan))
        quality = battery_data_quality(df)
        self.assertEqual(quality["status"], "unavailable")
        self.assertIsNone(quality["first_valid_timestamp"])

    def test_missing_values_are_counted_since_commissioning(self):
        stamps = cadence("2026-05-01 06:00", 4)
        quality = battery_data_quality(frame(stamps, [3800, np.nan, 3820, np.nan]))
        self.assertEqual(quality["valid_reading_count"], 2)
        self.assertEqual(quality["missing_since_commissioning"], 2)
        self.assertEqual(quality["completeness_since_commissioning"], 0.5)

    def test_zero_and_impossible_values_are_rejected(self):
        stamps = cadence("2026-05-01 06:00", 4)
        quality = battery_data_quality(frame(stamps, [3800, 0, 7000, 3900]))
        self.assertEqual(quality["valid_reading_count"], 2)
        self.assertEqual(quality["rejected_reading_count"], 2)
        self.assertEqual(quality["zero_reading_count"], 1)


class DailyAndTrendTests(unittest.TestCase):
    def test_valid_daily_metrics(self):
        stamps = cadence("2026-05-01 06:00", 4, minutes=60)
        df = frame(stamps, [3800, 3900, 3700, 4000])
        daily = compute_daily_battery_metrics(
            df, empty_outages(),
            reliability_daily=reliability_daily([stamps[0].date()]),
        )
        row = daily.iloc[0]
        self.assertAlmostEqual(row["battery_min_v"], 3.7)
        self.assertAlmostEqual(row["battery_max_v"], 4.0)
        self.assertAlmostEqual(row["battery_mean_v"], 3.85)
        self.assertAlmostEqual(row["battery_net_change_v"], 0.2)

    def test_insufficient_samples_do_not_produce_slope(self):
        rows = frame(cadence("2026-05-01 06:00", 2), [3800, 3810])
        rows["battery_voltage_v"] = rows["Battery Voltage"] / 1000
        self.assertTrue(math.isnan(fit_voltage_slope(rows)))

    def test_positive_slope(self):
        rows = frame(cadence("2026-05-01 06:00", MIN_TREND_SAMPLES), np.linspace(3700, 3900, MIN_TREND_SAMPLES))
        rows["battery_voltage_v"] = rows["Battery Voltage"] / 1000
        self.assertGreater(fit_voltage_slope(rows), 0)

    def test_negative_slope(self):
        rows = frame(cadence("2026-05-01 06:00", MIN_TREND_SAMPLES), np.linspace(3900, 3700, MIN_TREND_SAMPLES))
        rows["battery_voltage_v"] = rows["Battery Voltage"] / 1000
        self.assertLess(fit_voltage_slope(rows), 0)

    def test_rolling_slope_requires_and_uses_window_coverage(self):
        stamps = cadence("2026-05-01 00:00", 80, minutes=60)
        rolling = compute_rolling_battery_metrics(
            frame(stamps, np.linspace(3700, 4100, len(stamps)))
        )
        self.assertTrue(rolling["rolling_slope_72h_v_per_day"].iloc[:58].isna().all())
        self.assertFalse(math.isnan(rolling["rolling_slope_72h_v_per_day"].iloc[-1]))
        self.assertFalse(math.isnan(rolling["voltage_change_24h_v"].iloc[-1]))

    def test_operating_regime_boundary_controls_daytime_metric(self):
        stamps = cadence("2026-04-20 06:00", 12, minutes=60) + cadence("2026-04-21 06:00", 12, minutes=60)
        df = frame(stamps, np.linspace(3800, 4000, len(stamps)))
        days = [local("2026-04-20 00:00").date(), local("2026-04-21 00:00").date()]
        daily = compute_daily_battery_metrics(
            df, empty_outages(), reliability_daily=reliability_daily(days)
        )
        by_day = daily.set_index("date")
        self.assertTrue(math.isnan(by_day.loc[days[0], "daytime_voltage_change_v"]))
        self.assertFalse(math.isnan(by_day.loc[days[1], "daytime_voltage_change_v"]))

    def test_dst_sensitive_slope_uses_real_elapsed_time(self):
        start = local("2026-03-08 01:30")
        stamps = [start + timedelta(minutes=5 * index) for index in range(13)]
        rows = frame(stamps, np.linspace(3800, 3900, len(stamps)))
        rows["battery_voltage_v"] = rows["Battery Voltage"] / 1000
        # The series spans the spring-forward clock jump but one real hour.
        self.assertAlmostEqual(fit_voltage_slope(rows), 0.1, places=6)


class OutageContextTests(unittest.TestCase):
    def test_reliability_export_timestamps_are_halifax_wall_clock(self):
        with tempfile.TemporaryDirectory() as directory:
            one_outage().assign(
                gap_start="2026-05-02 12:00:00",
                gap_end="2026-05-02 14:00:00",
            ).to_csv(Path(directory) / "outage_intervals.csv", index=False)
            reliability_daily([local("2026-05-02 00:00").date()]).to_csv(
                Path(directory) / "daily_reliability.csv", index=False
            )
            outages, _ = load_reliability_exports(directory)
            self.assertEqual(outages["gap_start"].iloc[0], local("2026-05-02 12:00"))

    def test_stale_reliability_exports_are_rejected(self):
        df = frame(cadence("2026-05-01 06:00", 2), [3800, 3810])
        daily = reliability_daily([local("2026-05-01 00:00").date()])
        daily["rows_received"] = 1
        self.assertFalse(reliability_exports_match_data(df, daily))

    def test_outage_with_battery_context(self):
        before = cadence("2026-05-01 12:00", 25, minutes=60)
        after = cadence("2026-05-02 14:00", 7, minutes=60)
        df = frame(before + after, np.linspace(4000, 3700, len(before) + len(after)))
        context = compute_outage_battery_context(df, one_outage())
        self.assertTrue(bool(context["usable_pre_outage_context"].iloc[0]))
        self.assertFalse(math.isnan(context["pre_24h_latest_voltage_v"].iloc[0]))
        self.assertFalse(math.isnan(context["first_recovery_voltage_v"].iloc[0]))
        self.assertIn("not established", context["not_proven"].iloc[0])

    def test_outage_without_battery_context(self):
        df = frame(cadence("2026-05-03 00:00", 12), np.linspace(3800, 3900, 12))
        context = compute_outage_battery_context(df, one_outage())
        self.assertFalse(bool(context["usable_pre_outage_context"].iloc[0]))
        self.assertTrue(math.isnan(context["pre_24h_latest_voltage_v"].iloc[0]))

    def test_relationship_wording_is_not_causal(self):
        days = pd.date_range("2026-05-01", periods=12).date
        daily = pd.DataFrame({
            "battery_min_v": np.linspace(3.5, 4.1, 12),
            "battery_mean_v": np.linspace(3.6, 4.2, 12),
            "battery_last_v": np.linspace(3.5, 4.1, 12),
            "battery_net_change_v": np.linspace(-0.1, 0.1, 12),
            "daytime_voltage_change_v": np.linspace(-0.1, 0.1, 12),
            "rolling_72h_slope_v_per_day": np.linspace(-0.1, 0.1, 12),
            "telemetry_completeness": np.linspace(0.5, 1.0, 12),
            "significant_gap_count": [0, 1] * 6,
            "temperature_mean_c": np.linspace(5, 20, 12),
            "temperature_min_c": np.linspace(0, 15, 12),
        }, index=days)
        text = " ".join(item["interpretation"] for item in analyze_relationships(daily))
        self.assertIn("not causal", text)
        self.assertNotIn("caused", text)


class EnergyModelTests(unittest.TestCase):
    def test_model_unit_conversion(self):
        parameters = EnergyModelParameters(
            active_current_ma=10, sleep_current_ma=0.1,
            active_duration_seconds=10, sleep_duration_seconds=290,
            cycles_per_day=288, sensor_current_ma=1, radio_current_ma=1,
            battery_nominal_capacity_mah=2000, panel_rated_power_w=2,
            solar_equivalent_hours=4, charging_efficiency=0.8,
        )
        result = model_daily_power_budget(parameters)
        expected = 288 * ((12 * 10 + 0.1 * 290) / 3600)
        self.assertAlmostEqual(result["daily_charge_consumption_mah"], expected)
        self.assertAlmostEqual(result["solar_energy_input_wh"], 6.4)
        self.assertEqual(result["quantity_category"], "modeled")

    def test_zero_or_invalid_model_parameters(self):
        with self.assertRaises(ValueError):
            model_daily_power_budget(EnergyModelParameters(10, 0.1, 10, 290, 0))
        with self.assertRaises(ValueError):
            model_daily_power_budget(EnergyModelParameters(-1, 0.1, 10, 290, 288))
        with self.assertRaises(ValueError):
            model_daily_power_budget(EnergyModelParameters(0, 0, 10, 290, 288))


class GracefulUnavailableTests(unittest.TestCase):
    def test_unavailable_battery_does_not_break_analysis(self):
        df = frame(cadence("2026-05-01 06:00", 12), np.full(12, np.nan))
        with tempfile.TemporaryDirectory() as directory:
            summary, written = run_battery_analysis(df, directory, outages=empty_outages())
            self.assertEqual(summary["status"], "skipped")
            self.assertEqual(len(written), 1)
            payload = json.loads(Path(written[0]).read_text())
            self.assertEqual(payload["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
