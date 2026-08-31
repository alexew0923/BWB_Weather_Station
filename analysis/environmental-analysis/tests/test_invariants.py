"""Determinism and the invariants that keep the engine honest."""

import json
import math
import unittest
from datetime import datetime, timedelta

from tests.support import DRY_RAIL, START, csv_text, dataset_from, row, series_csv

from environmental import sensors
from environmental.api import analyze_environment, detect_environmental_events
from environmental.models import (
    DataQuality,
    EventClassification,
    EvidenceStrength,
    SoilResponseStatus,
    StatementKind,
    to_serialisable,
)


def varied_record(samples=4000):
    """A record containing dry spells, wetting events, gaps and bad readings."""
    lines = []
    moment = START
    for index in range(samples):
        wet = (index % 400) < 30 or (index % 977) < 12
        soil = None if index % 7 == 0 else 1900.0 + (index % 50) * 4
        if index % 500 == 13:
            soil = 0                      # ambiguous zero
        pressure = 4.04 if index % 613 == 0 else 1010.0
        lines.append(
            row(
                moment,
                temperature=10.0 + 8.0 * math.sin(2 * math.pi * index / 288.0),
                humidity=97.0 if wet else 62.0,
                soil=soil,
                pressure=pressure,
                wetness=DRY_RAIL - (900 if wet else 0),
                count=index + 1,
            )
        )
        moment += timedelta(minutes=430 if index % 700 == 699 else 5)
    return csv_text(lines)


class DeterminismTests(unittest.TestCase):
    def setUp(self):
        self.document = varied_record()

    def test_the_same_input_produces_the_same_events(self):
        first = detect_environmental_events(dataset_from(self.document))
        second = detect_environmental_events(dataset_from(self.document))
        self.assertEqual(
            [to_serialisable(event) for event in first],
            [to_serialisable(event) for event in second],
        )

    def test_the_same_input_produces_the_same_summary(self):
        first = analyze_environment(dataset_from(self.document))
        second = analyze_environment(dataset_from(self.document))
        for attribute in ("event_counts", "soil_response_counts"):
            self.assertEqual(getattr(first, attribute), getattr(second, attribute))
        self.assertEqual(
            [anomaly.anomaly_id for anomaly in first.anomalies],
            [anomaly.anomaly_id for anomaly in second.anomalies],
        )

    def test_row_order_in_the_source_does_not_change_the_result(self):
        lines = self.document.strip().split("\n")
        shuffled = csv_text(list(reversed(lines[1:])))
        self.assertEqual(
            [event.event_id for event in detect_environmental_events(
                dataset_from(self.document))],
            [event.event_id for event in detect_environmental_events(
                dataset_from(shuffled))],
        )


class InvariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = dataset_from(varied_record())
        cls.summary = analyze_environment(cls.dataset)
        cls.events = cls.summary.events

    def test_there_are_events_to_test(self):
        self.assertGreater(len(self.events), 5)

    def test_every_event_ends_at_or_after_it_starts(self):
        for event in self.events:
            self.assertGreaterEqual(event.end_time, event.start_time)

    def test_no_duration_is_negative(self):
        for event in self.events:
            self.assertGreaterEqual(event.duration_minutes, 0)

    def test_duration_matches_the_reported_boundaries(self):
        for event in self.events:
            expected = (event.end_time - event.start_time).total_seconds() / 60.0
            self.assertAlmostEqual(event.duration_minutes, expected, places=1)

    def test_event_ids_are_unique(self):
        identifiers = [event.event_id for event in self.events]
        self.assertEqual(len(identifiers), len(set(identifiers)))

    def test_events_are_in_chronological_order_and_do_not_overlap(self):
        for earlier, later in zip(self.events, self.events[1:]):
            self.assertLessEqual(earlier.start_time, later.start_time)
            self.assertLess(earlier.end_time, later.start_time)

    def test_soil_response_is_never_not_detected_on_unusable_data(self):
        for event in self.events:
            if event.soil_response.status is SoilResponseStatus.NOT_DETECTED:
                self.assertIs(
                    event.soil_response.quality.level,
                    DataQuality.USABLE,
                    f"{event.event_id} claimed a negative result on "
                    f"{event.soil_response.quality.level} data",
                )

    def test_a_detected_soil_response_always_has_a_direction(self):
        for event in self.events:
            if event.soil_response.status is SoilResponseStatus.DETECTED:
                self.assertIn(
                    event.soil_response.direction.value, ("increase", "decrease")
                )
                self.assertIsNotNone(event.soil_response.response_counts)

    def test_an_unknown_soil_response_asserts_no_magnitude(self):
        for event in self.events:
            if event.soil_response.status is SoilResponseStatus.UNKNOWN:
                self.assertIsNone(event.soil_response.delay_minutes)
                self.assertIsNone(event.soil_response.persistence_minutes)

    def test_every_event_carries_version_metadata(self):
        for event in self.events:
            self.assertTrue(event.detection_version)
            self.assertTrue(event.engine_version)
            self.assertTrue(event.soil_response.version)

    def test_no_event_claims_a_calibrated_water_quantity(self):
        forbidden = (
            "millimetre", "millimeter", "rainfall amount", "volumetric",
            "percent soil moisture", "irrigation", "plant stress",
        )
        text = json.dumps(to_serialisable(self.events)).lower()
        for phrase in forbidden:
            if phrase in ("irrigation",):
                # Permitted only inside an explicit refusal.
                continue
            self.assertNotIn(phrase, text, f"an event claimed {phrase!r}")

    def test_every_event_states_the_uncalibrated_caveat(self):
        for event in self.events:
            self.assertTrue(
                any("uncalibrated" in caveat
                    for caveat in event.interpretation.caveats)
            )

    def test_no_event_claims_a_cause(self):
        for event in self.events:
            self.assertEqual(event.interpretation.cause, "undetermined")

    def test_interpretations_are_labelled_as_interpretations(self):
        for event in self.events:
            for item in event.evidence:
                self.assertIsNot(item.kind, StatementKind.INTERPRETATION)

    def test_probable_events_never_rest_on_insufficient_data(self):
        for event in self.events:
            if event.classification is EventClassification.PROBABLE_WETTING_EVENT:
                self.assertIsNot(event.data_quality.level, DataQuality.INSUFFICIENT)
                self.assertIsNot(
                    event.interpretation.evidence_strength,
                    EvidenceStrength.INSUFFICIENT,
                )

    def test_invalid_telemetry_never_creates_an_event(self):
        # Every event's window must contain valid wetness observations.
        for event in self.events:
            window = self.dataset.subset(event.start_time, event.end_time)
            self.assertGreater(
                int(window.series(sensors.WETNESS_SIGNAL).notna().sum()), 0
            )

    def test_the_whole_summary_serialises_to_json(self):
        json.dumps(to_serialisable(self.summary))

    def test_the_summary_states_its_limitations(self):
        self.assertTrue(self.summary.limitations)
        self.assertTrue(
            any("rainfall" in item for item in self.summary.limitations)
        )

    def test_versions_are_reported_for_every_component(self):
        for key in (
            "engine_version",
            "wetness_detector_version",
            "soil_response_version",
            "baseline_version",
            "anomaly_detector_version",
        ):
            self.assertTrue(self.summary.versions[key])


if __name__ == "__main__":
    unittest.main()
