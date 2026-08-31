"""Stage 5/6: wetness-event detection, boundaries, merging and classification."""

import unittest
from datetime import datetime, timedelta

from tests.support import (
    DRY_RAIL,
    START,
    csv_text,
    dataset_from,
    humid_during,
    row,
    series_csv,
    wet_profile,
)

from environmental.api import detect_environmental_events, get_event, list_events
from environmental.config import EnvironmentalConfig, WetnessDetectorConfig
from environmental.errors import UnknownEventError
from environmental.events import detect_wetness_intervals, event_id_for
from environmental.models import EventClassification, EvidenceStrength


def detect(csv_document, config=None):
    dataset = dataset_from(csv_document, config=config)
    return dataset, detect_wetness_intervals(dataset, (config or dataset.config).wetness)


class DetectionTests(unittest.TestCase):
    def test_obvious_single_event_is_detected(self):
        _, intervals = detect(
            series_csv(START, 120, wetness=wet_profile(40, 24, 56))
        )
        self.assertEqual(len(intervals), 1)
        self.assertEqual(intervals[0].samples, 24)
        self.assertAlmostEqual(intervals[0].peak_deviation, 800.0)

    def test_a_perfectly_dry_record_produces_no_events(self):
        _, intervals = detect(series_csv(START, 120))
        self.assertEqual(intervals, [])

    def test_single_sample_spike_is_not_an_event(self):
        _, intervals = detect(
            series_csv(START, 120, wetness=wet_profile(50, 1, 69, depth=2000.0))
        )
        self.assertEqual(intervals, [])

    def test_two_sample_spike_is_not_an_event(self):
        _, intervals = detect(
            series_csv(START, 120, wetness=wet_profile(50, 2, 68, depth=2000.0))
        )
        self.assertEqual(intervals, [])

    def test_noisy_dry_baseline_below_the_threshold_produces_no_events(self):
        def noisy(index, _moment):
            return DRY_RAIL - (index % 3) * 8      # 0, 8 or 16 counts, all < 30
        _, intervals = detect(series_csv(START, 200, wetness=noisy))
        self.assertEqual(intervals, [])

    def test_two_separated_events_stay_separate(self):
        def profile(index, _moment):
            if 20 <= index < 40 or 80 <= index < 100:
                return DRY_RAIL - 900
            return DRY_RAIL
        _, intervals = detect(series_csv(START, 140, wetness=profile))
        self.assertEqual(len(intervals), 2)

    def test_two_close_intervals_are_merged(self):
        # A six-sample (30 minute) dry pause is inside the 60 minute merge gap.
        def profile(index, _moment):
            if 20 <= index < 40 or 46 <= index < 70:
                return DRY_RAIL - 900
            return DRY_RAIL
        _, intervals = detect(series_csv(START, 140, wetness=profile))
        self.assertEqual(len(intervals), 1)
        self.assertEqual(intervals[0].samples, 50)

    def test_hysteresis_prevents_flicker_around_the_entry_threshold(self):
        # The event opens cleanly, then the signal oscillates between 40 counts
        # (above the 30-count entry bar) and 20 counts (below entry, above the
        # 10-count exit bar). With a single threshold this fragments into a
        # dozen events; with hysteresis it stays one.
        def profile(index, _moment):
            if 100 <= index < 106:
                return DRY_RAIL - 900
            if 106 <= index < 140:
                return DRY_RAIL - (40 if index % 2 else 20)
            return DRY_RAIL
        _, intervals = detect(series_csv(START, 400, wetness=profile))
        self.assertEqual(len(intervals), 1)
        self.assertGreaterEqual(intervals[0].samples, 40)

    def test_slowly_drifting_baseline_is_tracked_not_detected(self):
        # The dry rail itself creeps down by 60 counts over three weeks.
        def drift(index, _moment):
            return DRY_RAIL - index * 0.01
        _, intervals = detect(
            series_csv(START, 6000, wetness=drift)
        )
        self.assertEqual(intervals, [])


def wet_lines(start, count, wet=False, first_count=1):
    """Regular rows at 5-minute spacing, all dry or all wet."""
    return [
        row(
            start + timedelta(minutes=5 * index),
            wetness=DRY_RAIL - (900 if wet else 0),
            humidity=97.0 if wet else 60.0,
            count=first_count + index,
        )
        for index in range(count)
    ]


class BoundaryTests(unittest.TestCase):
    """Boundaries are measured against a record that also contains dry data.

    The detector needs to have observed the dry state to have a reference for
    it; a synthetic record that is wet from end to end deliberately produces no
    events, which is the subject of its own test below.
    """

    def test_a_telemetry_gap_cuts_an_event_and_marks_it_censored(self):
        lines = wet_lines(START, 100)
        wet_start = START + timedelta(minutes=5 * 100)
        lines += wet_lines(wet_start, 20, wet=True, first_count=200)
        # The station is switched off for seven hours mid-event.
        resume = wet_start + timedelta(minutes=5 * 19 + 430)
        lines += wet_lines(resume, 20, wet=True, first_count=300)
        lines += wet_lines(resume + timedelta(minutes=5 * 20), 100, first_count=400)

        _, intervals = detect(csv_text(lines))
        self.assertEqual(len(intervals), 2)
        self.assertTrue(intervals[0].end_censored)
        self.assertTrue(intervals[1].start_censored)

    def test_an_outage_is_never_merged_across(self):
        lines = wet_lines(START, 100)
        wet_start = START + timedelta(minutes=5 * 100)
        lines += wet_lines(wet_start, 10, wet=True, first_count=200)
        resume = wet_start + timedelta(minutes=5 * 9 + 45)
        lines += wet_lines(resume, 10, wet=True, first_count=300)
        lines += wet_lines(resume + timedelta(minutes=5 * 10), 100, first_count=400)

        _, intervals = detect(csv_text(lines))
        self.assertEqual(
            len(intervals), 2, "a 45-minute hole is not observed dry time"
        )

    def test_an_event_at_the_edge_of_the_record_is_censored(self):
        lines = wet_lines(START, 100)
        lines += wet_lines(
            START + timedelta(minutes=5 * 100), 20, wet=True, first_count=200
        )
        _, intervals = detect(csv_text(lines))
        self.assertEqual(len(intervals), 1)
        self.assertFalse(intervals[0].start_censored)
        self.assertTrue(intervals[0].end_censored)

    def test_a_record_that_is_wet_throughout_yields_no_events(self):
        # There is no observed dry state to measure a departure from, so the
        # honest answer is "nothing detected", not a record-long event.
        _, intervals = detect(csv_text(wet_lines(START, 60, wet=True)))
        self.assertEqual(intervals, [])

    def test_event_crossing_midnight_is_one_event(self):
        start = datetime(2026, 5, 14, 18, 0)
        _, intervals = detect(
            series_csv(start, 200, wetness=wet_profile(60, 40, 100))
        )
        self.assertEqual(len(intervals), 1)
        self.assertNotEqual(
            intervals[0].start_time.date(), intervals[0].end_time.date()
        )

    def test_event_across_the_daylight_saving_fall_back_has_real_duration(self):
        # Both passes through the repeated 01:00 hour are present, as they are
        # in the real export. Wall-clock arithmetic would report 55 minutes for
        # an interval that really lasted 115.
        wall_clock = (
            [datetime(2025, 11, 2, 0, 30) + timedelta(minutes=5 * i) for i in range(6)]
            + [datetime(2025, 11, 2, 1, 0) + timedelta(minutes=5 * i) for i in range(12)]
            + [datetime(2025, 11, 2, 1, 0) + timedelta(minutes=5 * i) for i in range(12)]
            + [datetime(2025, 11, 2, 2, 0) + timedelta(minutes=5 * i) for i in range(24)]
        )
        lines = []
        for index, moment in enumerate(wall_clock):
            wet = 6 <= index < 42
            lines.append(
                row(moment, wetness=DRY_RAIL - (900 if wet else 0),
                    humidity=97.0 if wet else 60.0, count=index + 1)
            )
        _, intervals = detect(csv_text(lines))
        self.assertEqual(len(intervals), 1)
        wall_minutes = (wall_clock[41] - wall_clock[6]).total_seconds() / 60.0
        self.assertGreater(intervals[0].duration_minutes, wall_minutes)


class MissingDataTests(unittest.TestCase):
    def test_missing_wetness_readings_produce_no_events(self):
        # A completely absent channel is missing telemetry, never an event.
        _, intervals = detect(series_csv(START, 120, wetness=None))
        self.assertEqual(intervals, [])

    def test_invalid_wetness_readings_cannot_create_an_event(self):
        # 9999 is outside the plausible 0-4095 ADC range and must be masked.
        _, intervals = detect(
            series_csv(START, 120, wetness=lambda i, m: 9999 if 40 <= i < 60 else DRY_RAIL)
        )
        self.assertEqual(intervals, [])

    def test_blank_wetness_inside_an_event_does_not_split_it(self):
        # Two blank cells are a ten-minute hole, inside the continuity limit.
        def profile(index, _moment):
            if index in (45, 46):
                return None
            return DRY_RAIL - 900 if 40 <= index < 70 else DRY_RAIL
        _, intervals = detect(series_csv(START, 140, wetness=profile))
        self.assertEqual(len(intervals), 1)


class IdentityTests(unittest.TestCase):
    def test_event_ids_are_deterministic_and_carry_the_offset(self):
        identifier = event_id_for(
            dataset_from(series_csv(START, 3)).start_time
        )
        self.assertEqual(identifier, "wetting-2026-05-14T06:00-0300")

    def test_repeated_analysis_produces_identical_ids(self):
        document = series_csv(START, 120, wetness=wet_profile(40, 24, 56))
        first = detect_environmental_events(dataset_from(document))
        second = detect_environmental_events(dataset_from(document))
        self.assertEqual(
            [event.event_id for event in first], [event.event_id for event in second]
        )

    def test_ids_are_unique_within_an_analysis(self):
        def profile(index, _moment):
            return DRY_RAIL - 900 if index % 40 < 12 else DRY_RAIL
        events = detect_environmental_events(
            dataset_from(series_csv(START, 400, wetness=profile))
        )
        identifiers = [event.event_id for event in events]
        self.assertEqual(len(identifiers), len(set(identifiers)))

    def test_get_event_raises_a_domain_error_for_an_unknown_id(self):
        events = detect_environmental_events(
            dataset_from(series_csv(START, 120, wetness=wet_profile(40, 24, 56)))
        )
        with self.assertRaises(UnknownEventError):
            get_event(events, "wetting-1999-01-01T00:00-0400")


class ClassificationTests(unittest.TestCase):
    def build(self, depth=800.0, wet_samples=24, humidity=97.0):
        document = series_csv(
            START,
            120,
            wetness=wet_profile(40, wet_samples, 120 - 40 - wet_samples, depth=depth),
            humidity=humid_during(40, wet_samples, wet_humidity=humidity),
        )
        return detect_environmental_events(dataset_from(document))

    def test_corroborated_sustained_excursion_is_probable(self):
        events = self.build()
        self.assertEqual(len(events), 1)
        self.assertIs(
            events[0].classification, EventClassification.PROBABLE_WETTING_EVENT
        )
        self.assertIs(events[0].evidence_strength, EvidenceStrength.STRONG)

    def test_uncorroborated_excursion_is_only_a_candidate(self):
        events = self.build(humidity=45.0)
        self.assertIs(
            events[0].classification, EventClassification.CANDIDATE_WETTING_EVENT
        )
        self.assertTrue(
            any("humidity did not corroborate" in caveat
                for caveat in events[0].interpretation.caveats)
        )

    def test_small_uncorroborated_excursion_is_uncertain(self):
        events = self.build(depth=45.0, humidity=45.0)
        self.assertIs(
            events[0].classification, EventClassification.UNCERTAIN_WETTING_EVENT
        )

    def test_no_classification_claims_a_rainfall_amount(self):
        events = self.build()
        payload = events[0].to_dict()
        text = str(payload).lower()
        for forbidden in ("millimetre", "millimeter", " mm", "rainfall amount"):
            self.assertNotIn(forbidden, text)

    def test_events_carry_detector_version_metadata(self):
        events = self.build()
        self.assertTrue(events[0].detection_version)
        self.assertTrue(events[0].detection_method)
        self.assertTrue(events[0].engine_version)

    def test_list_events_filters_by_classification(self):
        events = self.build()
        self.assertEqual(
            len(list_events(events, classification="probable_wetting_event")), 1
        )
        self.assertEqual(
            len(list_events(events, classification="uncertain_wetting_event")), 0
        )


class ConfigurationTests(unittest.TestCase):
    def test_thresholds_are_overridable(self):
        document = series_csv(START, 120, wetness=wet_profile(40, 24, 56, depth=45.0))
        default = detect_environmental_events(dataset_from(document))
        self.assertEqual(len(default), 1)

        strict = EnvironmentalConfig().with_overrides(
            wetness=WetnessDetectorConfig(enter_counts=200.0, exit_counts=100.0)
        )
        dataset = dataset_from(document, config=strict)
        self.assertEqual(detect_environmental_events(dataset, strict), ())

    def test_inverted_polarity_is_supported(self):
        document = series_csv(
            START, 120, wetness=lambda i, m: 100.0 + (900.0 if 40 <= i < 64 else 0.0)
        )
        config = EnvironmentalConfig().with_overrides(
            wetness=WetnessDetectorConfig(wet_direction=1)
        )
        dataset = dataset_from(document, config=config)
        intervals = detect_wetness_intervals(dataset, config.wetness)
        self.assertEqual(len(intervals), 1)


if __name__ == "__main__":
    unittest.main()
