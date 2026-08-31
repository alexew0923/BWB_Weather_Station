"""Stage 7: soil-signal response analysis.

This is the most easily abused part of the engine, so it is the most heavily
gated.

What it can say
    "The soil signal moved away from its pre-event baseline by 240 ADC counts
    and stayed there for 45 minutes."

What it must never say
    "The soil absorbed 4 mm of water", "soil moisture rose to 32%", or "the
    plants were watered". The probe is uncalibrated and this repository holds
    no calibration curve, so none of those sentences has any support.

Three refusals are built in.

**Direction is reported, meaning is not.** No document in this repository, and
no pattern in the data, establishes whether a rising ADC count on this probe
means wetter soil or drier soil. Event-aligned changes in the historical record
split 46 positive to 38 negative, which is a coin flip. The engine therefore
reports ``INCREASE`` or ``DECREASE`` in raw counts and refuses to translate
that into "wetter" or "drier" unless a caller supplies a calibrated polarity.

**A diurnal confound is removed before testing.** In the densest month of the
record the soil signal tracks air temperature (Spearman +0.47) and swings about
165 counts between its 06:00 low and its 13:00 high -- as large as any
event-related change. Testing raw values against a raw baseline would report
the sunrise as a soil response every single day. The engine subtracts a
slowly-varying time-of-day profile before testing, and says whether it managed
to.

**Absence of data is never absence of response.** ``NOT_DETECTED`` is permitted
only when the windows were good enough that a response would have been visible.
Every other case is ``UNKNOWN``.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import sensors
from .baselines import rolling_hour_of_day_median
from .models import (
    DataQuality,
    Evidence,
    QualityAssessment,
    SignalDirection,
    SoilResponse,
    SoilResponseStatus,
    StatementKind,
)
from .quality import ambiguous_zero_fraction, assess_window
from .statistics import robust_sigma
from .version import SOIL_RESPONSE_VERSION


@dataclass(frozen=True)
class SoilContext:
    """Pre-computed soil series for a whole dataset.

    Built once per analysis run: the diurnal profile is the same for every
    event, and recomputing it per event would be the single most expensive
    thing this engine does.
    """

    raw: pd.Series
    residual: pd.Series
    diurnal_adjusted: bool
    note: str = ""


def build_soil_context(dataset, config=None):
    """Prepare the soil series, with its daily cycle removed where possible."""
    settings = config or dataset.config.soil
    raw = dataset.series(sensors.SOIL_SIGNAL)
    if not settings.apply_diurnal_adjustment or raw.dropna().empty:
        return SoilContext(
            raw=raw,
            residual=raw,
            diurnal_adjusted=False,
            note="diurnal adjustment disabled or no soil observations available",
        )

    profile = rolling_hour_of_day_median(
        raw,
        window_days=settings.diurnal_profile_window_days,
        min_samples=settings.diurnal_min_samples_per_hour,
    )
    covered = profile.notna() & raw.notna()
    if not covered.any():
        return SoilContext(
            raw=raw,
            residual=raw,
            diurnal_adjusted=False,
            note="the soil signal's time-of-day profile could not be estimated",
        )

    # Where the profile is estimable, work on the residual; elsewhere fall back
    # to the raw value so the series is not punched full of holes. Points that
    # fell back are visible as a lower adjusted fraction in the note.
    residual = raw.where(~covered, raw - profile + profile[covered].median())
    fraction = float(covered.sum() / max(1, int(raw.notna().sum())))
    return SoilContext(
        raw=raw,
        residual=residual,
        diurnal_adjusted=True,
        note=(
            f"time-of-day profile removed on {fraction:.0%} of valid soil "
            f"observations (window {settings.diurnal_profile_window_days:g} days)"
        ),
    )


def analyze_soil_response(dataset, interval, context=None, config=None):
    """Classify the soil signal's behaviour around one wetting event."""
    settings = config or dataset.config.soil
    context = context or build_soil_context(dataset, settings)

    pre_start = interval.start_time - pd.Timedelta(hours=settings.pre_event_window_hours)
    pre_end = interval.start_time
    post_end = interval.end_time + pd.Timedelta(hours=settings.post_event_window_hours)
    # A deviation may only be attributed to this event if it begins while the
    # surface is wet or shortly after it dries. The wider post-event window is
    # still used for the descriptive peak and trajectory.
    attribution_end = min(
        post_end,
        interval.end_time + pd.Timedelta(hours=settings.max_response_delay_hours),
    )

    baseline_quality = assess_window(
        dataset,
        sensors.SOIL_SIGNAL,
        pre_start,
        pre_end,
        min_samples=settings.min_baseline_samples,
        min_span_minutes=settings.min_baseline_span_minutes,
    )
    # The verdict is gated on the attribution window, because that is where a
    # response would have to appear for a negative result to mean anything.
    response_quality = assess_window(
        dataset,
        sensors.SOIL_SIGNAL,
        interval.start_time,
        attribution_end,
        min_samples=settings.min_response_samples,
    )

    zero_fraction = ambiguous_zero_fraction(
        dataset, sensors.SOIL_SIGNAL, pre_start, post_end
    )

    reasons = []
    if zero_fraction is not None and zero_fraction > settings.max_zero_fraction:
        reasons.append(
            f"{zero_fraction:.0%} of soil readings in the analysis window are "
            "ambiguous zeros, which cannot be told apart from a missing-value "
            "sentinel"
        )

    baseline_values = context.residual.loc[pre_start:pre_end].dropna()
    response_values = context.residual.loc[interval.start_time:post_end].dropna()

    blocking = (
        baseline_quality.level is not DataQuality.USABLE
        or response_quality.level is not DataQuality.USABLE
        or len(baseline_values) < settings.min_baseline_samples
        or len(response_values) < settings.min_response_samples
        or bool(reasons)
    )

    combined_quality = QualityAssessment(
        level=max(
            (baseline_quality.level, response_quality.level),
            key=lambda level: (
                0 if level is DataQuality.USABLE
                else 1 if level is DataQuality.PARTIALLY_USABLE
                else 2 if level is DataQuality.INSUFFICIENT
                else 3
            ),
        ),
        reasons=baseline_quality.reasons + response_quality.reasons + tuple(reasons),
        observations=baseline_quality.observations + response_quality.observations,
        valid_observations=len(baseline_values) + len(response_values),
        expected_observations=response_quality.expected_observations,
        telemetry_coverage=response_quality.telemetry_coverage,
        longest_gap_minutes=max(
            [value for value in (baseline_quality.longest_gap_minutes,
                                 response_quality.longest_gap_minutes)
             if value is not None],
            default=None,
        ),
    )

    if blocking:
        return SoilResponse(
            status=SoilResponseStatus.UNKNOWN,
            quality=combined_quality,
            diurnal_adjusted=context.diurnal_adjusted,
            baseline_samples=len(baseline_values),
            response_samples=len(response_values),
            ambiguous_zero_fraction=zero_fraction,
            evidence=(
                Evidence(
                    StatementKind.INTERPRETATION,
                    "Soil behaviour around this event cannot be determined: the "
                    "available soil telemetry is not good enough to show a "
                    "response or to rule one out.",
                ),
            ),
            version=SOIL_RESPONSE_VERSION,
        )

    baseline = float(baseline_values.median())
    sigma = robust_sigma(baseline_values, floor=settings.min_sigma_counts)
    threshold = max(
        settings.robust_sigma_multiple * sigma, settings.min_absolute_counts
    )

    deviations = response_values - baseline
    # Once started inside the attribution window a run may continue past it.
    qualifying = (deviations.abs() >= threshold) & (
        deviations.index <= attribution_end
    )
    run = _first_persistent_run(
        qualifying,
        np.sign(deviations.to_numpy()),
        response_values.index,
        settings.min_persistence_samples,
        dataset.config.quality.continuity_gap_minutes,
    )

    peak_position = deviations.abs().idxmax()
    peak_deviation = float(deviations.loc[peak_position])

    evidence = [
        Evidence(
            StatementKind.COMPUTED_MEASUREMENT,
            f"Pre-event soil baseline was {baseline:.0f} ADC counts over "
            f"{len(baseline_values)} valid observations.",
            quantity="soil_baseline",
            value=baseline,
            unit="raw ADC counts",
        ),
        Evidence(
            StatementKind.STATISTICAL_EVIDENCE,
            f"Baseline scatter (robust sigma) was {sigma:.0f} counts, giving a "
            f"detection threshold of {threshold:.0f} counts.",
            quantity="detection_threshold",
            value=threshold,
            unit="raw ADC counts",
        ),
    ]
    if context.diurnal_adjusted:
        evidence.append(
            Evidence(
                StatementKind.COMPUTED_MEASUREMENT,
                "The soil signal's time-of-day cycle was removed before testing; "
                + context.note,
            )
        )

    if run is None:
        # Only defensible because the windows passed the quality gate above.
        evidence.append(
            Evidence(
                StatementKind.STATISTICAL_EVIDENCE,
                f"The largest deviation from baseline in the whole post-event "
                f"window was {peak_deviation:+.0f} counts. No deviation reached "
                f"the {threshold:.0f}-count threshold for at least "
                f"{settings.min_persistence_samples} consecutive observations "
                f"within {settings.max_response_delay_hours:g} hours of the "
                "end of the wetting, which is the window in which a change may "
                "be attributed to it.",
                quantity="peak_deviation",
                value=peak_deviation,
                unit="raw ADC counts",
            )
        )
        return SoilResponse(
            status=SoilResponseStatus.NOT_DETECTED,
            quality=combined_quality,
            direction=SignalDirection.NONE,
            baseline_counts=baseline,
            baseline_sigma_counts=sigma,
            detection_threshold_counts=threshold,
            response_counts=peak_deviation,
            relative_change=_relative(peak_deviation, baseline),
            peak_counts=float(response_values.loc[peak_position]),
            diurnal_adjusted=context.diurnal_adjusted,
            baseline_samples=len(baseline_values),
            response_samples=len(response_values),
            ambiguous_zero_fraction=zero_fraction,
            evidence=tuple(evidence),
            version=SOIL_RESPONSE_VERSION,
        )

    start_position, end_position = run
    onset = response_values.index[start_position]
    run_end = response_values.index[end_position]
    run_deviations = deviations.iloc[start_position:end_position + 1]
    direction = (
        SignalDirection.INCREASE
        if float(run_deviations.median()) > 0
        else SignalDirection.DECREASE
    )
    delay = (onset - interval.start_time).total_seconds() / 60.0
    time_to_peak = (peak_position - interval.start_time).total_seconds() / 60.0
    persistence = (run_end - onset).total_seconds() / 60.0

    evidence.append(
        Evidence(
            StatementKind.COMPUTED_MEASUREMENT,
            f"The soil signal deviated from baseline by {peak_deviation:+.0f} "
            f"counts and held beyond the threshold for {persistence:.0f} minutes "
            f"from {onset.isoformat()}.",
            quantity="response_counts",
            value=peak_deviation,
            unit="raw ADC counts",
        )
    )
    evidence.append(
        Evidence(
            StatementKind.INTERPRETATION,
            "A soil-signal response was detected. The direction of the change is "
            "reported in raw counts only: this probe has no calibration in this "
            "repository, so the change cannot be called wetter or drier.",
        )
    )

    return SoilResponse(
        status=SoilResponseStatus.DETECTED,
        quality=combined_quality,
        direction=direction,
        baseline_counts=baseline,
        baseline_sigma_counts=sigma,
        detection_threshold_counts=threshold,
        response_counts=peak_deviation,
        relative_change=_relative(peak_deviation, baseline),
        delay_minutes=delay,
        peak_counts=float(response_values.loc[peak_position]),
        time_to_peak_minutes=time_to_peak,
        persistence_minutes=persistence,
        diurnal_adjusted=context.diurnal_adjusted,
        baseline_samples=len(baseline_values),
        response_samples=len(response_values),
        ambiguous_zero_fraction=zero_fraction,
        evidence=tuple(evidence),
        version=SOIL_RESPONSE_VERSION,
    )


def _relative(change, baseline):
    if baseline is None or not np.isfinite(baseline) or abs(baseline) < 1e-9:
        return None
    return float(change / baseline)


def _first_persistent_run(qualifying, signs, index, min_samples, continuity_minutes):
    """First run of qualifying samples that is long enough and unbroken.

    A run must share one direction and must not be stitched across a telemetry
    gap: samples either side of an outage are not consecutive observations of a
    sustained deviation, they are two observations with a hole between them.
    """
    flags = np.asarray(qualifying.to_numpy(), dtype=bool)
    if flags.size == 0:
        return None
    minutes = np.concatenate(
        ([0.0], index.to_series().diff().dropna().dt.total_seconds().to_numpy() / 60.0)
    )
    start = None
    for position in range(flags.size):
        broken = (
            position > 0
            and (
                minutes[position] > continuity_minutes
                or signs[position] != signs[position - 1]
            )
        )
        if not flags[position] or broken:
            if start is not None and position - start >= min_samples:
                return start, position - 1
            start = position if flags[position] else None
            continue
        if start is None:
            start = position
    if start is not None and flags.size - start >= min_samples:
        return start, flags.size - 1
    return None
