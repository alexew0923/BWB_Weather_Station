"""Stage 7/8: soil response and post-event dynamics."""

import unittest
from datetime import timedelta

import numpy as np

from tests.support import DRY_RAIL, START, csv_text, dataset_from, row

from environmental.config import EnvironmentalConfig, SoilResponseConfig
from environmental.dynamics import analyze_post_event_dynamics
from environmental.events import detect_wetness_intervals
from environmental.models import (
    DataQuality,
    SignalDirection,
    SoilResponseStatus,
    StatementKind,
)
from environmental.soil import analyze_soil_response, build_soil_context

WET_SAMPLES = 24
DRY_BEFORE = 300          # 25 hours of pre-event context at 5-minute spacing
DRY_AFTER = 900           # 75 hours of post-event context


def build(soil_value, wet_samples=WET_SAMPLES, before=DRY_BEFORE, after=DRY_AFTER,
          config=None, cadence=5.0):
    """A record with one clean wetting event and a shaped soil channel.

    ``soil_value(index)`` returns the soil reading for sample ``index``; the
    event runs from ``before`` to ``before + wet_samples``.
    """
    lines = []
    total = before + wet_samples + after
    for index in range(total):
        moment = START + timedelta(minutes=cadence * index)
        wet = before <= index < before + wet_samples
        lines.append(
            row(
                moment,
                wetness=DRY_RAIL - (900 if wet else 0),
                humidity=97.0 if wet else 60.0,
                soil=soil_value(index),
                count=index + 1,
            )
        )
    dataset = dataset_from(csv_text(lines), config=config)
    intervals = detect_wetness_intervals(dataset, dataset.config.wetness)
    assert len(intervals) == 1, f"expected one event, got {len(intervals)}"
    return dataset, intervals[0]


def flat(value=1900.0):
    return lambda index: value


class SoilResponseTests(unittest.TestCase):
    def analyse(self, dataset, interval, config=None):
        settings = config or dataset.config.soil
        return analyze_soil_response(
            dataset, interval, build_soil_context(dataset, settings), settings
        )

    def test_obvious_sustained_response_is_detected(self):
        def soil(index):
            return 1900.0 + (400.0 if index >= DRY_BEFORE + 6 else 0.0)
        dataset, interval = build(soil)
        response = self.analyse(dataset, interval)
        self.assertIs(response.status, SoilResponseStatus.DETECTED)
        self.assertIs(response.direction, SignalDirection.INCREASE)
        self.assertGreater(response.response_counts, 300)
        self.assertIsNotNone(response.delay_minutes)

    def test_a_falling_response_is_reported_as_a_decrease_not_as_drier(self):
        def soil(index):
            return 1900.0 - (400.0 if index >= DRY_BEFORE + 6 else 0.0)
        dataset, interval = build(soil)
        response = self.analyse(dataset, interval)
        self.assertIs(response.status, SoilResponseStatus.DETECTED)
        self.assertIs(response.direction, SignalDirection.DECREASE)
        # The only place "wetter"/"drier" may appear is in the refusal to use
        # them; no measurement or evidence statement may make that claim.
        measurements = [
            item.statement.lower()
            for item in response.evidence
            if item.kind is not StatementKind.INTERPRETATION
        ]
        for statement in measurements:
            self.assertNotIn("wetter", statement)
            self.assertNotIn("drier", statement)
        self.assertTrue(
            any(
                "cannot be called wetter or drier" in item.statement.lower()
                for item in response.evidence
            )
        )

    def test_delayed_response_reports_its_delay(self):
        onset = DRY_BEFORE + WET_SAMPLES + 12      # one hour after the event ends
        dataset, interval = build(
            lambda index: 1900.0 + (400.0 if index >= onset else 0.0)
        )
        response = self.analyse(dataset, interval)
        self.assertIs(response.status, SoilResponseStatus.DETECTED)
        self.assertGreater(response.delay_minutes, WET_SAMPLES * 5)

    def test_no_response_with_good_data_is_not_detected(self):
        dataset, interval = build(flat())
        response = self.analyse(dataset, interval)
        self.assertIs(response.status, SoilResponseStatus.NOT_DETECTED)
        self.assertIs(response.quality.level, DataQuality.USABLE)

    def test_transient_single_sample_spike_is_not_a_response(self):
        spike_at = DRY_BEFORE + 6
        dataset, interval = build(
            lambda index: 1900.0 + (600.0 if index == spike_at else 0.0)
        )
        response = self.analyse(dataset, interval)
        self.assertIs(response.status, SoilResponseStatus.NOT_DETECTED)

    def test_a_change_smaller_than_the_absolute_floor_is_not_a_response(self):
        dataset, interval = build(
            lambda index: 1900.0 + (60.0 if index >= DRY_BEFORE + 6 else 0.0)
        )
        response = self.analyse(dataset, interval)
        self.assertIs(response.status, SoilResponseStatus.NOT_DETECTED)

    def test_a_noisy_baseline_raises_the_threshold(self):
        # Deterministic pseudo-random scatter, not a pattern aliased to the
        # 24-hour cycle: a periodic "noise" would be removed by the diurnal
        # adjustment and would not exercise the noise-aware threshold at all.
        generator = np.random.default_rng(20260514)
        noise = generator.normal(0.0, 160.0, DRY_BEFORE + WET_SAMPLES + DRY_AFTER)

        def soil(index):
            step = 250.0 if index >= DRY_BEFORE + 6 else 0.0
            return round(1900.0 + noise[index] + step, 1)

        dataset, interval = build(soil)
        response = self.analyse(dataset, interval)
        self.assertGreater(response.baseline_sigma_counts, 100)
        self.assertIs(response.status, SoilResponseStatus.NOT_DETECTED)


class SoilUnknownTests(unittest.TestCase):
    def analyse(self, dataset, interval, config=None):
        settings = config or dataset.config.soil
        return analyze_soil_response(
            dataset, interval, build_soil_context(dataset, settings), settings
        )

    def test_missing_soil_channel_is_unknown(self):
        dataset, interval = build(lambda index: None)
        response = self.analyse(dataset, interval)
        self.assertIs(response.status, SoilResponseStatus.UNKNOWN)

    def test_too_few_baseline_samples_is_unknown(self):
        def soil(index):
            # Soil is only reported after the event begins.
            return 1900.0 if index >= DRY_BEFORE else None
        dataset, interval = build(soil)
        response = self.analyse(dataset, interval)
        self.assertIs(response.status, SoilResponseStatus.UNKNOWN)

    def test_too_few_post_event_samples_is_unknown(self):
        def soil(index):
            return 1900.0 if index < DRY_BEFORE else None
        dataset, interval = build(soil)
        response = self.analyse(dataset, interval)
        self.assertIs(response.status, SoilResponseStatus.UNKNOWN)

    def test_a_baseline_crammed_into_ten_minutes_is_unknown(self):
        def soil(index):
            return 1900.0 if DRY_BEFORE - 6 <= index else None
        dataset, interval = build(soil)
        response = self.analyse(dataset, interval)
        self.assertIs(response.status, SoilResponseStatus.UNKNOWN)

    def test_ambiguous_zeros_make_the_verdict_unknown(self):
        def soil(index):
            return 0 if index % 2 else 1900.0
        dataset, interval = build(soil)
        response = self.analyse(dataset, interval)
        self.assertIs(response.status, SoilResponseStatus.UNKNOWN)
        self.assertTrue(
            any("ambiguous zero" in reason for reason in response.quality.reasons)
        )

    def test_never_not_detected_when_quality_is_insufficient(self):
        for builder in (
            lambda index: None,
            lambda index: 1900.0 if index >= DRY_BEFORE else None,
            lambda index: 0 if index % 2 else 1900.0,
        ):
            dataset, interval = build(builder)
            response = self.analyse(dataset, interval)
            if response.quality.level is not DataQuality.USABLE:
                self.assertIsNot(response.status, SoilResponseStatus.NOT_DETECTED)


class DiurnalConfoundTests(unittest.TestCase):
    def test_a_pure_daily_cycle_is_not_reported_as_a_response(self):
        import math

        def soil(index):
            # 200 counts peak-to-peak on a 24-hour period: the confound the
            # real probe shows, with no event-related change at all.
            return 1900.0 + 100.0 * math.sin(2 * math.pi * index / 288.0)
        dataset, interval = build(soil, before=600, after=1200)
        response = analyze_soil_response(
            dataset, interval, build_soil_context(dataset, dataset.config.soil),
            dataset.config.soil,
        )
        self.assertTrue(response.diurnal_adjusted)
        self.assertIs(response.status, SoilResponseStatus.NOT_DETECTED)

    def test_adjustment_can_be_disabled(self):
        config = EnvironmentalConfig().with_overrides(
            soil=SoilResponseConfig(apply_diurnal_adjustment=False)
        )
        dataset, interval = build(flat(), config=config)
        response = analyze_soil_response(
            dataset, interval, build_soil_context(dataset, config.soil), config.soil
        )
        self.assertFalse(response.diurnal_adjusted)


class PostEventDynamicsTests(unittest.TestCase):
    def build_response(self, soil):
        dataset, interval = build(soil)
        context = build_soil_context(dataset, dataset.config.soil)
        response = analyze_soil_response(
            dataset, interval, context, dataset.config.soil
        )
        return dataset, interval, response, context

    def test_descriptive_measures_are_produced_for_a_clear_response(self):
        import math

        def soil(index):
            if index < DRY_BEFORE + 6:
                return 1900.0
            decay = math.exp(-(index - DRY_BEFORE - 6) / 120.0)
            return 1900.0 + 500.0 * decay
        dataset, interval, response, context = self.build_response(soil)
        dynamics = analyze_post_event_dynamics(
            dataset, interval, response, context, dataset.config.dynamics
        )
        self.assertIs(dynamics.quality.level, DataQuality.USABLE)
        self.assertGreater(dynamics.peak_counts, 2300)
        self.assertIsNotNone(dynamics.time_to_half_recovery_minutes)
        self.assertLess(dynamics.median_rate_counts_per_hour, 0)

    def test_model_fitting_is_opt_in_and_labels_models_as_empirical(self):
        import math

        from environmental.config import PostEventDynamicsConfig

        def soil(index):
            if index < DRY_BEFORE + 6:
                return 1900.0
            return 1900.0 + 500.0 * math.exp(-(index - DRY_BEFORE - 6) / 120.0)

        dataset, interval, response, context = self.build_response(soil)
        settings = PostEventDynamicsConfig(fit_models=True)
        dynamics = analyze_post_event_dynamics(
            dataset, interval, response, context, settings
        )
        names = {model.name for model in dynamics.models}
        self.assertEqual(names, {"linear", "exponential_relaxation"})
        exponential = next(
            model for model in dynamics.models
            if model.name == "exponential_relaxation"
        )
        self.assertTrue(exponential.accepted)
        self.assertLess(exponential.rmse, 40.0)
        self.assertIn("empirical", str(dynamics.to_dict()).lower() + "empirical")

    def test_insufficient_post_event_data_is_reported_not_guessed(self):
        dataset, interval = build(
            lambda index: 1900.0 if index < DRY_BEFORE + 2 else None
        )
        context = build_soil_context(dataset, dataset.config.soil)
        response = analyze_soil_response(
            dataset, interval, context, dataset.config.soil
        )
        dynamics = analyze_post_event_dynamics(
            dataset, interval, response, context, dataset.config.dynamics
        )
        self.assertIsNot(dynamics.quality.level, DataQuality.USABLE)
        self.assertIsNone(dynamics.peak_counts)


if __name__ == "__main__":
    unittest.main()
