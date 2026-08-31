"""Stage 10: environmental anomalies."""

import math
import unittest
from datetime import datetime

from tests.support import dataset_from, series_csv

from environmental import sensors
from environmental.anomalies import (
    detect_persistence_anomalies,
    detect_rate_anomalies,
    detect_value_anomalies,
)
from environmental.config import AnomalyConfig
from environmental.models import AnomalyKind, EvidenceStrength

BASE = datetime(2026, 5, 1, 0, 0)


def steady_temperature(index, moment):
    """A smooth, repeatable daily cycle with a small deterministic wobble.

    Smooth on purpose: a sawtooth that resets at midnight is a real step change
    and would be flagged as a rate anomaly, correctly but unhelpfully.
    """
    hour = moment.hour + moment.minute / 60.0
    wobble = ((index * 7919) % 100) / 100.0 - 0.5
    return round(12.0 + 6.0 * math.sin(2 * math.pi * hour / 24.0) + wobble, 2)


class ValueAnomalyTests(unittest.TestCase):
    def test_ordinary_variation_produces_no_anomalies(self):
        dataset = dataset_from(series_csv(BASE, 8640, temperature=steady_temperature))
        self.assertEqual(
            detect_value_anomalies(dataset, sensors.TEMPERATURE, AnomalyConfig()), []
        )

    def test_an_obvious_excursion_is_flagged(self):
        spike = range(4000, 4030)

        def temperature(index, moment):
            value = steady_temperature(index, moment)
            return value + 25.0 if index in spike else value

        dataset = dataset_from(series_csv(BASE, 8640, temperature=temperature))
        found = detect_value_anomalies(dataset, sensors.TEMPERATURE, AnomalyConfig())
        self.assertTrue(found)
        self.assertIs(found[0].kind, AnomalyKind.HIGH_TEMPERATURE)
        self.assertGreater(abs(found[0].robust_z), 3.5)

    def test_consecutive_anomalous_samples_collapse_into_one_finding(self):
        spike = range(4000, 4060)

        def temperature(index, moment):
            value = steady_temperature(index, moment)
            return value + 25.0 if index in spike else value

        dataset = dataset_from(series_csv(BASE, 8640, temperature=temperature))
        found = detect_value_anomalies(dataset, sensors.TEMPERATURE, AnomalyConfig())
        self.assertEqual(len(found), 1)

    def test_insufficient_history_produces_no_verdict(self):
        dataset = dataset_from(series_csv(BASE, 20, temperature=steady_temperature))
        self.assertEqual(
            detect_value_anomalies(dataset, sensors.TEMPERATURE, AnomalyConfig()), []
        )

    def test_every_anomaly_carries_its_evidence_and_its_caveat(self):
        def temperature(index, moment):
            value = steady_temperature(index, moment)
            return value - 25.0 if 4000 <= index < 4020 else value

        dataset = dataset_from(series_csv(BASE, 8640, temperature=temperature))
        found = detect_value_anomalies(dataset, sensors.TEMPERATURE, AnomalyConfig())
        self.assertTrue(found)
        anomaly = found[0]
        self.assertIs(anomaly.kind, AnomalyKind.LOW_TEMPERATURE)
        self.assertGreaterEqual(len(anomaly.evidence), 3)
        self.assertTrue(anomaly.interpretation.caveats)
        self.assertGreaterEqual(anomaly.history_samples, 30)

    def test_the_baseline_is_conditional_on_time_of_day(self):
        # 25 degrees is ordinary at midday here and impossible at 03:00. A
        # whole-record baseline would not notice; a time-of-day baseline does.
        night = range(3780, 3800)      # 2026-05-14, 03:00 onwards

        def temperature(index, moment):
            base = 5.0 if moment.hour < 6 else 25.0
            value = base + ((index * 7919) % 100) / 100.0 - 0.5
            return 25.0 if index in night else value

        dataset = dataset_from(series_csv(BASE, 8640, temperature=temperature))
        self.assertTrue(
            all(
                stamp.hour < 6
                for stamp in dataset.timestamps[3780:3800]
            ),
            "fixture sanity: the spiked samples must be night-time samples",
        )
        found = detect_value_anomalies(dataset, sensors.TEMPERATURE, AnomalyConfig())
        self.assertTrue(
            any(anomaly.time.hour < 6 for anomaly in found),
            "a daytime-normal value at 03:00 must be judged against 03:00 peers",
        )


class RateAnomalyTests(unittest.TestCase):
    def test_a_smooth_series_has_no_rate_anomalies(self):
        dataset = dataset_from(series_csv(BASE, 3000, temperature=steady_temperature))
        self.assertEqual(
            detect_rate_anomalies(dataset, sensors.TEMPERATURE, AnomalyConfig()), []
        )

    def test_a_step_change_is_flagged(self):
        def temperature(index, moment):
            value = steady_temperature(index, moment)
            return value + 20.0 if index >= 1500 else value

        dataset = dataset_from(series_csv(BASE, 3000, temperature=temperature))
        found = detect_rate_anomalies(dataset, sensors.TEMPERATURE, AnomalyConfig())
        self.assertEqual(len(found), 1)
        self.assertIs(found[0].kind, AnomalyKind.RAPID_TEMPERATURE_CHANGE)

    def test_a_rate_anomaly_is_not_called_a_sensor_fault(self):
        def temperature(index, moment):
            value = steady_temperature(index, moment)
            return value + 20.0 if index >= 1500 else value

        dataset = dataset_from(series_csv(BASE, 3000, temperature=temperature))
        found = detect_rate_anomalies(dataset, sensors.TEMPERATURE, AnomalyConfig())
        text = " ".join(found[0].interpretation.caveats).lower()
        self.assertIn("sensor health is analysed separately", text)


class PersistenceAnomalyTests(unittest.TestCase):
    def test_too_few_events_produce_no_verdict(self):
        self.assertEqual(detect_persistence_anomalies([], AnomalyConfig()), [])

    def test_the_longest_events_are_flagged(self):
        from environmental.api import detect_environmental_events
        from tests.support import DRY_RAIL

        def profile(index, _moment):
            position = index % 500
            if position < 12:
                return DRY_RAIL - 900
            if index > 9000 and position < 200:
                return DRY_RAIL - 900
            return DRY_RAIL

        dataset = dataset_from(series_csv(BASE, 12000, wetness=profile))
        events = detect_environmental_events(dataset)
        self.assertGreaterEqual(len(events), 10)
        found = detect_persistence_anomalies(events, AnomalyConfig())
        self.assertTrue(found)
        self.assertIs(found[0].kind, AnomalyKind.PERSISTENT_WETNESS)
        self.assertIs(found[0].evidence_strength, EvidenceStrength.MODERATE)


if __name__ == "__main__":
    unittest.main()
