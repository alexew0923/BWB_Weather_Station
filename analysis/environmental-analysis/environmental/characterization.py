"""Stage 6: event characterisation and classification.

Turns a detected interval into a fully described ``EnvironmentalEvent``: what
the wetness channel did, what the other sensors were doing before, during and
after, how good the data was, and -- kept strictly separate from all of that --
what may reasonably be concluded.

The classification ladder is deliberately short and has no intensity classes:

``probable_wetting_event``
    A sustained, substantial excursion with independent corroboration from
    relative humidity, on data good enough to trust.

``candidate_wetting_event``
    The excursion is real but one leg is missing: it is brief, or small, or the
    humidity channel does not corroborate it.

``uncertain_wetting_event``
    The interval was detected on data too thin, too censored or too gappy to
    support a firmer statement.

There is no ``heavy_rain``, no ``light_shower`` and no millimetre figure,
because the sensor cannot support any of them.
"""

import numpy as np
import pandas as pd

from . import sensors
from .events import DETECTION_METHOD, event_id_for
from .models import (
    DataQuality,
    EnvironmentalEvent,
    EventBoundaries,
    EventClassification,
    EventType,
    Evidence,
    EvidenceStrength,
    Interpretation,
    StatementKind,
    WetnessObservations,
    WindowComparison,
)
from .quality import assess_window
from .statistics import describe
from .version import ENGINE_VERSION, WETNESS_DETECTOR_VERSION

CONTEXT_SENSORS = (sensors.TEMPERATURE, sensors.HUMIDITY, sensors.PRESSURE)


def characterize_event(dataset, interval, deviation_frame, soil_response,
                       dynamics=None, config=None):
    """Build the full domain object for one detected interval."""
    settings = config or dataset.config
    wetness_config = settings.wetness

    pre_start = interval.start_time - pd.Timedelta(
        hours=settings.soil.pre_event_window_hours
    )
    post_end = interval.end_time + pd.Timedelta(
        hours=settings.soil.post_event_window_hours
    )

    observations = _wetness_observations(deviation_frame, interval)
    context = {
        sensor: _compare_windows(dataset, sensor, interval, pre_start, post_end)
        for sensor in CONTEXT_SENSORS
        if dataset.valid_count(sensor)
    }

    event_quality = assess_window(
        dataset,
        sensors.WETNESS_SIGNAL,
        interval.start_time,
        interval.end_time,
        min_samples=wetness_config.min_event_samples,
    )

    humidity = context.get(sensors.HUMIDITY)
    corroboration = _humidity_corroboration(dataset, interval, wetness_config)

    warnings = []
    if interval.start_censored:
        warnings.append(
            "the event was already in progress when telemetry resumed, so its "
            "true start is earlier than reported"
        )
    if interval.end_censored:
        warnings.append(
            "telemetry stopped before the event ended, so its true end is later "
            "than reported"
        )
    if (
        interval.largest_internal_gap_minutes is not None
        and interval.largest_internal_gap_minutes
        > settings.quality.continuity_gap_minutes
    ):
        warnings.append(
            f"the event contains a "
            f"{interval.largest_internal_gap_minutes:.0f}-minute telemetry gap"
        )

    classification, strength, caveats = _classify(
        interval, observations, event_quality, corroboration, wetness_config
    )

    evidence = _build_evidence(
        interval, observations, corroboration, context, event_quality
    )

    interpretation = Interpretation(
        statement=_statement(classification, interval, observations),
        evidence_strength=strength,
        cause="undetermined",
        caveats=tuple(caveats) + (
            "the wetness channel is an uncalibrated ADC signal; no rainfall "
            "quantity can be derived from it",
            "the source of the water -- rain, dew, fog, snowmelt or irrigation "
            "-- is not observable by this station",
        ),
    )

    return EnvironmentalEvent(
        event_id=event_id_for(interval.start_time),
        event_type=EventType.WETTING,
        station_id=settings.station_id,
        start_time=interval.start_time,
        end_time=interval.end_time,
        duration_minutes=round(interval.duration_minutes, 2),
        classification=classification,
        interpretation=interpretation,
        observations=observations,
        boundaries=EventBoundaries(
            start_censored=interval.start_censored,
            end_censored=interval.end_censored,
            gap_before_minutes=interval.gap_before_minutes,
            gap_after_minutes=interval.gap_after_minutes,
            largest_internal_gap_minutes=interval.largest_internal_gap_minutes,
        ),
        data_quality=event_quality,
        soil_response=soil_response,
        post_event_dynamics=dynamics,
        context=context,
        evidence=tuple(evidence),
        warnings=tuple(warnings),
        detection_method=DETECTION_METHOD,
        detection_version=WETNESS_DETECTOR_VERSION,
        engine_version=ENGINE_VERSION,
    )


# --------------------------------------------------------------------------


def _wetness_observations(frame, interval):
    window = frame.loc[interval.start_time:interval.end_time]
    deviation = window["deviation"].dropna()
    signal = window["signal"].dropna()
    reference = window["reference"].dropna()
    if deviation.empty:
        return WetnessObservations(samples=0, duration_minutes=interval.duration_minutes)

    peak_position = deviation.idxmax()
    minutes = (deviation.index - deviation.index[0]).total_seconds().to_numpy() / 60.0
    # Trapezoidal integral of deviation against time. The unit is count-minutes,
    # a property of this sensor's output. It is emphatically not a water depth.
    integrated = float(np.trapezoid(deviation.to_numpy(), minutes)) if len(deviation) > 1 else 0.0

    time_to_peak = float((peak_position - interval.start_time).total_seconds() / 60.0)
    onset_rate = (
        float(deviation.loc[peak_position] / time_to_peak) if time_to_peak > 0 else None
    )
    tail = deviation.loc[peak_position:]
    recovery_minutes = (
        (tail.index[-1] - peak_position).total_seconds() / 60.0 if len(tail) > 1 else 0.0
    )
    recovery_rate = (
        float((tail.iloc[-1] - tail.iloc[0]) / recovery_minutes)
        if recovery_minutes > 0
        else None
    )

    return WetnessObservations(
        dry_reference_counts=float(reference.median()) if not reference.empty else None,
        minimum_signal_counts=float(signal.min()) if not signal.empty else None,
        peak_deviation_counts=float(deviation.max()),
        mean_deviation_counts=float(deviation.mean()),
        median_deviation_counts=float(deviation.median()),
        integrated_deviation_count_minutes=integrated,
        time_to_peak_minutes=time_to_peak,
        onset_rate_counts_per_minute=onset_rate,
        recovery_rate_counts_per_minute=recovery_rate,
        samples=int(len(deviation)),
        duration_minutes=round(interval.duration_minutes, 2),
    )


def _compare_windows(dataset, sensor, interval, pre_start, post_end):
    semantics = sensors.SEMANTICS[sensor]
    pre = dataset.series(sensor).loc[pre_start:interval.start_time].dropna()
    during = dataset.series(sensor).loc[interval.start_time:interval.end_time].dropna()
    post = dataset.series(sensor).loc[interval.end_time:post_end].dropna()

    change = (
        float(during.median() - pre.median())
        if not during.empty and not pre.empty
        else None
    )
    post_change = (
        float(post.median() - during.median())
        if not post.empty and not during.empty
        else None
    )
    return WindowComparison(
        sensor=sensor,
        unit=semantics.unit,
        pre_event=describe(pre),
        during_event=describe(during),
        post_event=describe(post),
        change_from_baseline=change,
        post_event_change=post_change,
        quality=assess_window(
            dataset, sensor, interval.start_time, interval.end_time, min_samples=1
        ),
    )


def _humidity_corroboration(dataset, interval, config):
    """Independent evidence that the surface really was wet.

    Relative humidity is a separate sensor on a separate bus. If it is high
    while the wetness channel is depressed, two independent instruments agree.
    """
    values = dataset.series(sensors.HUMIDITY).loc[
        interval.start_time:interval.end_time
    ].dropna()
    if values.empty:
        return {
            "available": False,
            "corroborates": None,
            "fraction_at_or_above": None,
            "median": None,
            "threshold": config.corroborating_humidity_pct,
        }
    fraction = float((values >= config.corroborating_humidity_pct).mean())
    return {
        "available": True,
        "corroborates": bool(fraction >= config.corroborating_humidity_fraction),
        "fraction_at_or_above": fraction,
        "median": float(values.median()),
        "threshold": config.corroborating_humidity_pct,
    }


def _classify(interval, observations, quality, corroboration, config):
    caveats = []
    peak = observations.peak_deviation_counts or 0.0
    duration = interval.duration_minutes

    if quality.level in (DataQuality.INSUFFICIENT, DataQuality.INVALID):
        caveats.append("the event window's telemetry was not good enough to interpret")
        return (
            EventClassification.UNCERTAIN_WETTING_EVENT,
            EvidenceStrength.WEAK,
            caveats,
        )

    censored = interval.start_censored or interval.end_censored
    substantial = (
        peak >= config.probable_min_peak_counts
        and duration >= config.probable_min_duration_minutes
    )
    corroborated = bool(corroboration.get("corroborates"))

    if not corroboration.get("available"):
        caveats.append(
            "no valid humidity readings were available to corroborate the "
            "wetness signal"
        )

    if quality.level is DataQuality.PARTIALLY_USABLE:
        caveats.append("the event window has incomplete telemetry")

    if substantial and corroborated and quality.level is DataQuality.USABLE:
        strength = EvidenceStrength.STRONG if not censored else EvidenceStrength.MODERATE
        if censored:
            caveats.append(
                "the event's true extent is longer than observed because "
                "telemetry was interrupted at one or both ends"
            )
        return EventClassification.PROBABLE_WETTING_EVENT, strength, caveats

    if substantial and corroborated:
        caveats.append(
            "classified as probable on partially complete telemetry"
        )
        return (
            EventClassification.PROBABLE_WETTING_EVENT,
            EvidenceStrength.MODERATE,
            caveats,
        )

    if substantial or corroborated:
        if not substantial:
            caveats.append(
                f"the excursion peaked at {peak:.0f} counts over {duration:.0f} "
                f"minutes, below the "
                f"{config.probable_min_peak_counts:.0f}-count / "
                f"{config.probable_min_duration_minutes:.0f}-minute bar for a "
                "probable event"
            )
        if not corroborated and corroboration.get("available"):
            caveats.append(
                "relative humidity did not corroborate the wetness signal"
            )
        return (
            EventClassification.CANDIDATE_WETTING_EVENT,
            EvidenceStrength.MODERATE if substantial else EvidenceStrength.WEAK,
            caveats,
        )

    caveats.append(
        "the excursion is small and uncorroborated; it may be sensor noise, "
        "condensation on the board, or debris"
    )
    return (
        EventClassification.UNCERTAIN_WETTING_EVENT,
        EvidenceStrength.WEAK,
        caveats,
    )


def _build_evidence(interval, observations, corroboration, context, quality):
    evidence = [
        Evidence(
            StatementKind.RAW_OBSERVATION,
            f"The wetness signal reached a minimum of "
            f"{observations.minimum_signal_counts:.0f} ADC counts across "
            f"{observations.samples} observations."
            if observations.minimum_signal_counts is not None
            else "No valid wetness observations were available in the interval.",
            quantity="minimum_signal_counts",
            value=observations.minimum_signal_counts,
            unit="raw ADC counts",
        ),
        Evidence(
            StatementKind.COMPUTED_MEASUREMENT,
            f"That is {observations.peak_deviation_counts:.0f} counts away from "
            f"the dry reference level of "
            f"{observations.dry_reference_counts:.0f} counts, sustained for "
            f"{interval.duration_minutes:.0f} minutes."
            if observations.peak_deviation_counts is not None
            else "No deviation from the dry reference could be computed.",
            quantity="peak_deviation_counts",
            value=observations.peak_deviation_counts,
            unit="raw ADC counts",
        ),
    ]

    if corroboration.get("available"):
        evidence.append(
            Evidence(
                StatementKind.STATISTICAL_EVIDENCE,
                f"Relative humidity was at or above "
                f"{corroboration['threshold']:.0f}% for "
                f"{corroboration['fraction_at_or_above']:.0%} of the interval "
                f"(median {corroboration['median']:.1f}%), which is independent "
                "evidence of a wet surface.",
                quantity="humidity_fraction_at_threshold",
                value=corroboration["fraction_at_or_above"],
                unit="fraction",
            )
        )

    temperature = context.get(sensors.TEMPERATURE)
    if temperature is not None and temperature.change_from_baseline is not None:
        evidence.append(
            Evidence(
                StatementKind.COMPUTED_MEASUREMENT,
                f"Air temperature during the event differed from its "
                f"pre-event median by "
                f"{temperature.change_from_baseline:+.1f} degrees C.",
                quantity="temperature_change_c",
                value=temperature.change_from_baseline,
                unit="degrees C",
            )
        )

    if quality.telemetry_coverage is not None:
        evidence.append(
            Evidence(
                StatementKind.STATISTICAL_EVIDENCE,
                f"Telemetry coverage across the event window was "
                f"{quality.telemetry_coverage:.0%} of scheduled transmissions.",
                quantity="telemetry_coverage",
                value=quality.telemetry_coverage,
                unit="fraction",
            )
        )
    return evidence


def _statement(classification, interval, observations):
    duration = interval.duration_minutes
    if classification is EventClassification.PROBABLE_WETTING_EVENT:
        return (
            "A probable environmental wetting event: the wetness signal left its "
            f"dry reference and stayed away from it for {duration:.0f} minutes, "
            "with independent corroboration from relative humidity."
        )
    if classification is EventClassification.CANDIDATE_WETTING_EVENT:
        return (
            "A candidate wetting event: the wetness signal left its dry "
            f"reference for {duration:.0f} minutes, but the supporting evidence "
            "is incomplete."
        )
    return (
        "An uncertain wetting signal: an excursion was detected, but the "
        "evidence does not support calling it an environmental wetting event."
    )
