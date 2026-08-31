"""Current and recent environmental state, including every degraded case."""

import io
import unittest
from datetime import datetime, timedelta

import pandas as pd

from tests.support import HEADER, START, dataset_from, series_csv

from environmental.api import (
    detect_environmental_events,
    get_current_environmental_state,
    get_recent_environmental_state,
)
from environmental.config import EnvironmentalConfig
from environmental.models import FreshnessState


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
        return False


def opener_returning(text):
    def opener(url, timeout=None):
        return FakeResponse(text.encode("utf-8"))
    return opener


def opener_raising(error):
    def opener(url, timeout=None):
        raise error
    return opener


LIVE_ENV = {"STATIONWATCH_SHEET_URL": "https://example.test/live.csv"}


class RecentStateTests(unittest.TestCase):
    def test_fresh_telemetry_is_reported_as_current(self):
        dataset = dataset_from(series_csv(START, 60))
        state = get_recent_environmental_state(
            dataset, now=dataset.end_time + pd.Timedelta(minutes=4)
        )
        self.assertIs(state.freshness, FreshnessState.CURRENT)
        self.assertLess(state.data_age_minutes, 5)

    def test_old_telemetry_is_reported_as_stale_not_as_now(self):
        dataset = dataset_from(series_csv(START, 60))
        state = get_recent_environmental_state(
            dataset, now=dataset.end_time + pd.Timedelta(days=30)
        )
        self.assertIs(state.freshness, FreshnessState.STALE)
        self.assertIn("not current", state.summary)

    def test_every_reading_carries_its_own_observation_time(self):
        dataset = dataset_from(series_csv(START, 60))
        state = get_recent_environmental_state(dataset, now=dataset.end_time)
        for reading in state.readings.values():
            if reading.value is not None:
                self.assertIsNotNone(reading.observed_at)

    def test_uncalibrated_readings_are_labelled(self):
        dataset = dataset_from(series_csv(START, 60))
        state = get_recent_environmental_state(dataset, now=dataset.end_time)
        self.assertFalse(state.readings["wetness_signal_raw"].calibrated)
        self.assertTrue(state.readings["temperature_c"].calibrated)

    def test_a_single_observation_is_insufficient(self):
        dataset = dataset_from(series_csv(START, 1))
        state = get_recent_environmental_state(dataset, now=dataset.end_time)
        self.assertIs(state.freshness, FreshnessState.INSUFFICIENT_DATA)

    def test_an_event_in_progress_is_surfaced(self):
        from tests.support import DRY_RAIL, wet_profile

        dataset = dataset_from(
            series_csv(START, 200, wetness=wet_profile(150, 50, 0, depth=900.0))
        )
        events = detect_environmental_events(dataset)
        state = get_recent_environmental_state(
            dataset, now=dataset.end_time, events=events
        )
        self.assertTrue(state.active_wetness["in_progress"])
        self.assertIn("wetting event was in progress", state.summary)

    def test_serialises_without_pandas_objects(self):
        dataset = dataset_from(series_csv(START, 60))
        payload = get_recent_environmental_state(
            dataset, now=dataset.end_time
        ).to_dict()
        import json

        json.dumps(payload)


class LiveLoadingTests(unittest.TestCase):
    def setUp(self):
        self.config = EnvironmentalConfig()

    def test_header_only_live_sheet_is_awaiting_telemetry(self):
        state = get_current_environmental_state(
            config=self.config,
            opener=opener_returning(HEADER + "\n"),
            environ=dict(LIVE_ENV),
        )
        self.assertIs(state.freshness, FreshnessState.AWAITING_TELEMETRY)
        self.assertIn("no observations", state.summary)

    def test_unreachable_live_sheet_is_unavailable_not_an_environment_claim(self):
        from urllib.error import URLError

        state = get_current_environmental_state(
            config=self.config,
            opener=opener_raising(URLError("down")),
            environ=dict(LIVE_ENV),
        )
        self.assertIs(state.freshness, FreshnessState.UNAVAILABLE)
        self.assertEqual(state.readings, {})
        self.assertIsNone(state.latest_observation_at)

    def test_missing_configuration_is_unavailable(self):
        state = get_current_environmental_state(config=self.config, environ={})
        self.assertIs(state.freshness, FreshnessState.UNAVAILABLE)
        self.assertIn("STATIONWATCH_SHEET_URL", str(state.quality.reasons))

    def test_html_response_is_unavailable(self):
        state = get_current_environmental_state(
            config=self.config,
            opener=opener_returning("<!DOCTYPE html><html></html>"),
            environ=dict(LIVE_ENV),
        )
        self.assertIs(state.freshness, FreshnessState.UNAVAILABLE)

    def test_valid_live_sheet_produces_a_state(self):
        recent = datetime.now() - timedelta(minutes=6)
        document = series_csv(recent.replace(microsecond=0), 5, interval_minutes=1.0)
        state = get_current_environmental_state(
            config=self.config,
            opener=opener_returning(document),
            environ=dict(LIVE_ENV),
        )
        self.assertIn(
            state.freshness, (FreshnessState.CURRENT, FreshnessState.STALE)
        )
        self.assertIsNotNone(state.latest_observation_at)

    def test_the_live_source_ignores_the_local_development_override(self):
        state = get_current_environmental_state(
            config=self.config,
            opener=opener_returning(HEADER + "\n"),
            environ={**LIVE_ENV, "BWB_ENVIRONMENTAL_CSV": "/tmp/nope.csv"},
        )
        self.assertIs(state.freshness, FreshnessState.AWAITING_TELEMETRY)


if __name__ == "__main__":
    unittest.main()
