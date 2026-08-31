"""Stage 3/9: quality gating and environmental baselines."""

import unittest
from datetime import datetime, timedelta

from tests.support import DRY_RAIL, START, csv_text, dataset_from, row, series_csv

from environmental import sensors
from environmental.api import get_environmental_baseline
from environmental.baselines import diurnal_profile, dry_reference
from environmental.models import DataQuality, EvidenceStrength
from environmental.quality import (
    ambiguous_zero_fraction,
    assess_window,
    dataset_quality,
    gap_segments,
    interpolation_is_permitted,
    worst_quality,
)


class QualityGateTests(unittest.TestCase):
    def test_a_dense_complete_window_is_usable(self):
        dataset = dataset_from(series_csv(START, 200))
        assessment = assess_window(
            dataset, sensors.TEMPERATURE, dataset.start_time, dataset.end_time,
            min_samples=10,
        )
        self.assertIs(assessment.level, DataQuality.USABLE)
        self.assertEqual(assessment.reasons, ())

    def test_a_window_with_no_rows_is_insufficient_not_quiet(self):
        dataset = dataset_from(series_csv(START, 50))
        far = dataset.end_time + timedelta(days=30)
        assessment = assess_window(
            dataset, sensors.TEMPERATURE, far, far + timedelta(hours=1)
        )
        self.assertIs(assessment.level, DataQuality.INSUFFICIENT)
        self.assertIn("no telemetry rows in the window", assessment.reasons)

    def test_a_sensor_absent_from_present_rows_is_insufficient(self):
        dataset = dataset_from(series_csv(START, 50, pressure=None))
        assessment = assess_window(
            dataset, sensors.PRESSURE, dataset.start_time, dataset.end_time
        )
        self.assertIs(assessment.level, DataQuality.INSUFFICIENT)

    def test_too_few_samples_is_insufficient_even_on_perfect_telemetry(self):
        dataset = dataset_from(series_csv(START, 6))
        assessment = assess_window(
            dataset, sensors.TEMPERATURE, dataset.start_time, dataset.end_time,
            min_samples=50,
        )
        self.assertIs(assessment.level, DataQuality.INSUFFICIENT)

    def test_a_short_span_is_insufficient_for_a_baseline(self):
        dataset = dataset_from(series_csv(START, 8))
        assessment = assess_window(
            dataset, sensors.TEMPERATURE, dataset.start_time, dataset.end_time,
            min_samples=4, min_span_minutes=180.0,
        )
        self.assertIs(assessment.level, DataQuality.INSUFFICIENT)

    def test_a_long_outage_downgrades_a_window(self):
        lines = [row(START + timedelta(minutes=5 * i), count=i + 1) for i in range(60)]
        resume = START + timedelta(minutes=5 * 59 + 600)
        lines += [row(resume + timedelta(minutes=5 * i), count=100 + i)
                  for i in range(60)]
        dataset = dataset_from(csv_text(lines))
        assessment = assess_window(
            dataset, sensors.TEMPERATURE, dataset.start_time, dataset.end_time
        )
        self.assertIsNot(assessment.level, DataQuality.USABLE)
        self.assertTrue(
            any("outage" in reason for reason in assessment.reasons)
        )

    def test_worst_quality_keeps_the_worst_level_and_all_reasons(self):
        dataset = dataset_from(series_csv(START, 50))
        good = assess_window(
            dataset, sensors.TEMPERATURE, dataset.start_time, dataset.end_time
        )
        bad = assess_window(
            dataset, sensors.TEMPERATURE, dataset.start_time, dataset.end_time,
            min_samples=10_000,
        )
        combined = worst_quality(good, bad)
        self.assertIs(combined.level, DataQuality.INSUFFICIENT)
        self.assertTrue(combined.reasons)

    def test_negative_conclusions_need_usable_data(self):
        self.assertTrue(DataQuality.USABLE.supports_negative_conclusion)
        for level in (
            DataQuality.PARTIALLY_USABLE,
            DataQuality.INSUFFICIENT,
            DataQuality.INVALID,
        ):
            self.assertFalse(level.supports_negative_conclusion)

    def test_gap_segments_split_at_telemetry_holes(self):
        lines = [row(START + timedelta(minutes=5 * i), count=i + 1) for i in range(10)]
        resume = START + timedelta(minutes=5 * 9 + 120)
        lines += [row(resume + timedelta(minutes=5 * i), count=50 + i)
                  for i in range(10)]
        dataset = dataset_from(csv_text(lines))
        self.assertEqual(len(gap_segments(dataset)), 2)

    def test_interpolation_is_disabled_by_default(self):
        dataset = dataset_from(series_csv(START, 10))
        self.assertFalse(interpolation_is_permitted(dataset.config, 5.0))

    def test_ambiguous_zero_fraction_is_measured(self):
        dataset = dataset_from(
            series_csv(START, 10, soil=lambda i, m: 0 if i < 5 else 1900)
        )
        fraction = ambiguous_zero_fraction(
            dataset, sensors.SOIL_SIGNAL, dataset.start_time, dataset.end_time
        )
        self.assertAlmostEqual(fraction, 0.5)

    def test_dataset_quality_reports_low_coverage(self):
        lines = [row(START + timedelta(minutes=60 * i), count=i + 1)
                 for i in range(24)]
        assessment = dataset_quality(dataset_from(csv_text(lines)))
        self.assertIsNot(assessment.level, DataQuality.USABLE)
        self.assertTrue(any("coverage" in reason for reason in assessment.reasons))


class DryReferenceTests(unittest.TestCase):
    def test_the_reference_tracks_the_dry_rail_through_a_wet_spell(self):
        def profile(index, _moment):
            # Two continuous days wet inside a two-week record.
            return DRY_RAIL - 900 if 2000 <= index < 2576 else DRY_RAIL
        dataset = dataset_from(series_csv(START, 4032, wetness=profile))
        reference = dry_reference(
            dataset.series(sensors.WETNESS_SIGNAL).dropna(), dataset.config.wetness
        )
        self.assertEqual(reference.min(), DRY_RAIL)

    def test_the_reference_follows_a_drifting_dry_rail(self):
        def profile(index, _moment):
            return DRY_RAIL if index < 2000 else DRY_RAIL - 40
        dataset = dataset_from(series_csv(START, 4000, wetness=profile))
        reference = dry_reference(
            dataset.series(sensors.WETNESS_SIGNAL).dropna(), dataset.config.wetness
        )
        self.assertLess(reference.iloc[-1], DRY_RAIL)


class BaselineTests(unittest.TestCase):
    def test_a_sparse_period_is_labelled_weak_evidence(self):
        lines = [row(START + timedelta(minutes=60 * i), count=i + 1)
                 for i in range(200)]
        dataset = dataset_from(csv_text(lines))
        baseline = get_environmental_baseline(dataset)
        self.assertTrue(baseline.periods)
        for period in baseline.periods:
            self.assertIn(
                period.evidence_strength,
                (EvidenceStrength.WEAK, EvidenceStrength.MODERATE,
                 EvidenceStrength.INSUFFICIENT),
            )

    def test_a_dense_period_carries_its_coverage(self):
        dataset = dataset_from(series_csv(datetime(2026, 5, 1, 0, 0), 8000))
        baseline = get_environmental_baseline(dataset)
        may = next(period for period in baseline.periods if period.period == "2026-05")
        self.assertIsNotNone(may.telemetry_coverage)
        self.assertGreater(may.telemetry_coverage, 0.5)

    def test_uneven_sampling_does_not_break_the_baseline(self):
        lines = []
        moment = START
        for index in range(500):
            lines.append(row(moment, count=index + 1))
            moment += timedelta(minutes=5 if index % 3 else 37)
        baseline = get_environmental_baseline(dataset_from(csv_text(lines)))
        self.assertTrue(baseline.periods)

    def test_a_long_outage_is_not_filled_in(self):
        lines = [row(START + timedelta(minutes=5 * i), count=i + 1)
                 for i in range(300)]
        resume = START + timedelta(days=20)
        lines += [row(resume + timedelta(minutes=5 * i), count=1000 + i)
                  for i in range(300)]
        dataset = dataset_from(csv_text(lines))
        self.assertEqual(len(dataset), 600)
        self.assertGreater(dataset.coverage()["longest_gap_minutes"], 20000)

    def test_diurnal_aggregation_requires_a_minimum_bucket_size(self):
        dataset = dataset_from(series_csv(START, 40))
        profile = diurnal_profile(dataset.series(sensors.TEMPERATURE), min_samples=100)
        self.assertEqual(profile, {})

    def test_diurnal_profile_is_computed_when_dense_enough(self):
        dataset = dataset_from(
            series_csv(
                datetime(2026, 5, 1), 2880,
                temperature=lambda i, m: 10.0 + 8.0 * (m.hour / 23.0),
            )
        )
        profile = diurnal_profile(dataset.series(sensors.TEMPERATURE), min_samples=5)
        self.assertEqual(len(profile), 24)
        self.assertGreater(profile[23].median, profile[0].median)


if __name__ == "__main__":
    unittest.main()
