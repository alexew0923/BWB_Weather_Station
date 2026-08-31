"""Stage 3: the data-quality engine.

Nothing downstream may interpret a number that has not passed through here.
The engine distinguishes six states that are routinely conflated:

1. **station-wide telemetry absence** -- no row arrived at all;
2. **individual sensor absence** -- the row arrived with that field blank;
3. **malformed readings** -- a cell that is not a number;
4. **physically impossible readings** -- outside the audit's plausible range,
   or on a frame whose boot counter proves the buffer was corrupt;
5. **uncertain readings** -- present and in range, but ambiguous, such as a
   stored soil zero from before the ingestion script began blanking zeros;
6. **valid observations**.

Only state 6 may be interpreted. States 1-5 are counted and explained.

The rule that matters most: a quality verdict must be able to say
"insufficient". A window with no observations supports no conclusion at all --
not "nothing happened", which is a conclusion.
"""

import numpy as np
import pandas as pd

from . import sensors
from .models import DataQuality, QualityAssessment


def _coverage_level(fraction, config):
    if fraction is None:
        return None
    if fraction >= config.usable_coverage_fraction:
        return DataQuality.USABLE
    if fraction >= config.partial_coverage_fraction:
        return DataQuality.PARTIALLY_USABLE
    return DataQuality.INSUFFICIENT


def assess_window(dataset, sensor, start, end, min_samples=1, min_span_minutes=0.0):
    """Judge whether one sensor's data in ``[start, end]`` may be interpreted.

    The verdict is the *worst* of three independent checks -- telemetry
    coverage, sensor validity and sample count -- and every check that fired is
    named in ``reasons`` so a person can see why.
    """
    config = dataset.config.quality
    window = dataset.subset(start, end)
    coverage = window.coverage(start, end)
    values = window.series(sensor)
    valid = int(values.notna().sum())
    received = len(window)

    reasons = []
    levels = []

    fraction = coverage["fraction"]
    coverage_level = _coverage_level(fraction, config)
    if coverage_level is not None:
        levels.append(coverage_level)
        if coverage_level is not DataQuality.USABLE:
            reasons.append(
                f"telemetry coverage {fraction:.0%} of scheduled transmissions"
            )

    longest_gap = coverage["longest_gap_minutes"]
    if longest_gap is not None and longest_gap > config.outage_gap_minutes:
        levels.append(DataQuality.PARTIALLY_USABLE)
        reasons.append(
            f"window contains a {longest_gap:.0f}-minute telemetry outage"
        )
    elif longest_gap is not None and longest_gap > config.continuity_gap_minutes:
        reasons.append(
            f"window contains a {longest_gap:.0f}-minute telemetry gap"
        )

    if received == 0:
        levels.append(DataQuality.INSUFFICIENT)
        reasons.append("no telemetry rows in the window")
    elif valid == 0:
        levels.append(DataQuality.INSUFFICIENT)
        reasons.append(f"no valid {sensor} readings among {received} received rows")
    else:
        sensor_fraction = valid / received
        if sensor_fraction < config.partial_coverage_fraction:
            levels.append(DataQuality.INSUFFICIENT)
            reasons.append(
                f"{sensor} valid on only {sensor_fraction:.0%} of received rows"
            )
        elif sensor_fraction < config.usable_coverage_fraction:
            levels.append(DataQuality.PARTIALLY_USABLE)
            reasons.append(
                f"{sensor} valid on {sensor_fraction:.0%} of received rows"
            )

    if valid < min_samples:
        levels.append(DataQuality.INSUFFICIENT)
        reasons.append(f"{valid} valid observation(s), {min_samples} required")

    if min_span_minutes and valid:
        present = values.dropna()
        span = (present.index[-1] - present.index[0]).total_seconds() / 60.0
        if span < min_span_minutes:
            levels.append(DataQuality.INSUFFICIENT)
            reasons.append(
                f"valid observations span {span:.0f} minutes, "
                f"{min_span_minutes:.0f} required"
            )

    level = _worst(levels) if levels else DataQuality.USABLE
    return QualityAssessment(
        level=level,
        reasons=tuple(reasons),
        observations=received,
        valid_observations=valid,
        expected_observations=coverage["expected"],
        telemetry_coverage=fraction,
        longest_gap_minutes=longest_gap,
    )


_ORDER = {
    DataQuality.USABLE: 0,
    DataQuality.PARTIALLY_USABLE: 1,
    DataQuality.INSUFFICIENT: 2,
    DataQuality.INVALID: 3,
}


def _worst(levels):
    return max(levels, key=lambda level: _ORDER[level])


def worst_quality(*assessments):
    """Combine assessments, keeping the worst level and all of the reasons."""
    present = [item for item in assessments if item is not None]
    if not present:
        return QualityAssessment(level=DataQuality.INSUFFICIENT,
                                 reasons=("no assessment available",))
    level = _worst([item.level for item in present])
    reasons = tuple(reason for item in present for reason in item.reasons)
    return QualityAssessment(
        level=level,
        reasons=reasons,
        observations=max(item.observations for item in present),
        valid_observations=min(item.valid_observations for item in present),
        expected_observations=next(
            (item.expected_observations for item in present
             if item.expected_observations is not None),
            None,
        ),
        telemetry_coverage=next(
            (item.telemetry_coverage for item in present
             if item.telemetry_coverage is not None),
            None,
        ),
        longest_gap_minutes=max(
            (item.longest_gap_minutes for item in present
             if item.longest_gap_minutes is not None),
            default=None,
        ),
    )


def dataset_quality(dataset):
    """An overall verdict on a whole dataset."""
    coverage = dataset.coverage()
    reasons = []
    levels = []
    fraction = coverage["fraction"]
    level = _coverage_level(fraction, dataset.config.quality)
    if level is not None:
        levels.append(level)
        if level is not DataQuality.USABLE:
            reasons.append(
                f"overall telemetry coverage is {fraction:.0%} of scheduled "
                "transmissions across the record"
            )
    for sensor in dataset.sensor_columns:
        valid_fraction = dataset.valid_fraction(sensor)
        if valid_fraction == 0:
            reasons.append(f"{sensor} has no valid observations")
        elif valid_fraction < dataset.config.quality.partial_coverage_fraction:
            reasons.append(
                f"{sensor} is valid on only {valid_fraction:.0%} of received rows"
            )
    return QualityAssessment(
        level=_worst(levels) if levels else DataQuality.USABLE,
        reasons=tuple(reasons),
        observations=len(dataset),
        valid_observations=int(
            min((dataset.valid_count(s) for s in dataset.sensor_columns), default=0)
        ),
        expected_observations=coverage["expected"],
        telemetry_coverage=fraction,
        longest_gap_minutes=coverage["longest_gap_minutes"],
    )


def ambiguous_zero_fraction(dataset, sensor, start, end):
    """Fraction of present readings in a window that are ambiguous zeros.

    Applies to the raw ADC channels only. The ingestion script blanks zero
    values, so a stored zero predates that behaviour and cannot be told apart
    from a sentinel. This is reported rather than resolved.
    """
    window = dataset.subset(start, end)
    raw = window.series(sensor, valid_only=False)
    present = raw.notna()
    if not present.any():
        return None
    return float(((raw == 0) & present).sum() / present.sum())


def gap_segments(dataset, minutes=None):
    """Split a dataset's index into segments of uninterrupted telemetry.

    Returns a list of ``(start, end)`` timestamp pairs. Analysis that assumes
    signal continuity -- rolling statistics, event boundaries, response
    persistence -- must respect these boundaries: a station that was switched
    off did not hold its last reading for seven hours.
    """
    minutes = (
        dataset.config.quality.continuity_gap_minutes if minutes is None else minutes
    )
    index = dataset.timestamps
    if len(index) == 0:
        return []
    gaps = dataset.inter_arrival_minutes()
    breaks = np.flatnonzero(gaps.to_numpy() > minutes) + 1
    starts = np.concatenate(([0], breaks))
    ends = np.concatenate((breaks, [len(index)]))
    return [(index[a], index[b - 1]) for a, b in zip(starts, ends) if b > a]


def interpolation_is_permitted(config, gap_minutes):
    """Whether a single missing sample may be filled for a descriptive figure.

    Interpolation is disabled by default. When it is enabled the gap ceiling is
    small and the filled points are never allowed to create, extend or end an
    event -- detection always runs on observed samples only.
    """
    limit = config.quality.max_interpolation_gap_minutes
    return bool(limit) and gap_minutes is not None and gap_minutes <= limit
