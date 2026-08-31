"""Stage 5: wetness-event detection.

The algorithm, in full
---------------------

1. Take only *valid* wetness observations. Missing telemetry is never filled.
2. Estimate the dry reference level (see :mod:`.baselines`) and compute, for
   every observation, the deviation away from dry in the wet direction.
3. Walk the observations in order through a hysteresis state machine:

   * an event **opens** after ``enter_persistence_samples`` consecutive
     observations whose deviation is at least ``enter_counts``, and it is
     backdated to the first of those samples;
   * an event **closes** after ``exit_persistence_samples`` consecutive
     observations whose deviation is below ``exit_counts``, and it ends at the
     last sample before that run;
   * a telemetry gap longer than ``max_internal_gap_minutes`` **cuts** the
     interval. The station being switched off is not the end of a wetting
     event, and it is not evidence that one continued either, so the interval
     is closed at the last observed sample and both sides are flagged as
     censored.

4. Intervals separated by less than ``merge_gap_minutes`` of observed dry time
   are merged: a shower that pauses for twenty minutes is one event.
5. Intervals shorter than ``min_event_duration_minutes`` or thinner than
   ``min_event_samples`` are discarded.

Why hysteresis and persistence, and not a single threshold: a single threshold
turns one noisy sample into an event and turns a signal hovering at the
boundary into dozens of events. Two thresholds plus a persistence requirement
remove both failure modes without needing to smooth the data, which would blur
the event boundaries the analysis is trying to measure.

Why not a robust z-score
------------------------

The obvious modern choice would be ``z = (x - median) / (1.4826 * MAD)``. It is
the wrong tool for this signal. The dry state is a hard ADC rail: 82% of all
valid readings in the historical record sit at exactly 4095, and 90% of
consecutive differences within the dry state are exactly zero. The MAD of a
dry window is therefore exactly 0, so the z-score is either undefined or
infinite, and any floor placed under it is simply an absolute threshold wearing
a statistical costume. An absolute deviation from an adaptive dry reference is
what the data actually supports, and it is honest about being a chosen
threshold. See the README for the sensitivity analysis behind the default.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import sensors
from .baselines import wetness_deviation
from .models import EventType
from .version import WETNESS_DETECTOR_VERSION

DETECTION_METHOD = "hysteresis-persistence deviation from adaptive dry reference"


@dataclass(frozen=True)
class DetectedInterval:
    """A raw detected interval, before characterisation."""

    start_time: pd.Timestamp
    end_time: pd.Timestamp
    samples: int
    peak_deviation: float
    start_censored: bool
    end_censored: bool
    gap_before_minutes: float | None
    gap_after_minutes: float | None
    largest_internal_gap_minutes: float | None

    @property
    def duration_minutes(self):
        return (self.end_time - self.start_time).total_seconds() / 60.0


def _minutes(later, earlier):
    return (later - earlier).total_seconds() / 60.0


def _interval_minutes(index):
    """Minutes between consecutive entries of a DatetimeIndex slice.

    Computed through pandas timedelta arithmetic rather than raw integer views:
    the integer resolution of a pandas DatetimeIndex is not fixed, so dividing
    ``asi8`` by a hard-coded constant silently changes units between versions.
    """
    if len(index) < 2:
        return np.array([], dtype="float64")
    return (
        index.to_series().diff().dropna().dt.total_seconds().to_numpy() / 60.0
    )


def _scan(times, deviations, config):
    """Hysteresis state machine over ordered valid observations.

    A deliberate Python loop: the state depends on the previous state, so a
    vectorised form would need a scan primitive that is harder to read than the
    thing it replaces. It is O(n) over ~25 000 rows.
    """
    intervals = []
    open_at = None          # index where the current event started
    candidate = None        # index of the first sample of a qualifying run
    above = 0
    below = 0
    last_index = None

    def close(end_index, censored):
        intervals.append((open_at, end_index, censored))

    for index in range(len(times)):
        if last_index is not None:
            gap = _minutes(times[index], times[last_index])
            if gap > config.max_internal_gap_minutes:
                if open_at is not None:
                    close(last_index, True)
                    open_at = None
                candidate = None
                above = below = 0
        value = deviations[index]

        if open_at is None:
            if value >= config.enter_counts:
                above += 1
                if candidate is None:
                    candidate = index
                if above >= config.enter_persistence_samples:
                    open_at = candidate
                    below = 0
            else:
                above = 0
                candidate = None
        else:
            if value < config.exit_counts:
                below += 1
                if below >= config.exit_persistence_samples:
                    close(index - below, False)
                    open_at = None
                    above = 0
                    candidate = None
            else:
                below = 0
        last_index = index

    if open_at is not None:
        close(len(times) - 1, True)
    return intervals


def detect_wetness_intervals(dataset, config=None, deviation_frame=None):
    """Detect wetting intervals in a dataset. Deterministic for fixed input."""
    settings = config or dataset.config.wetness
    frame = deviation_frame if deviation_frame is not None else wetness_deviation(
        dataset, settings
    )
    present = frame["deviation"].dropna()
    if present.empty:
        return []

    times = present.index
    values = present.to_numpy(dtype="float64")
    raw = _scan(times, values, settings)
    if not raw:
        return []

    # -- merge intervals separated by only a short observed dry spell --------
    merged = []
    for start_index, end_index, censored_end in raw:
        if merged:
            previous_start, previous_end, previous_censored = merged[-1]
            separation = _minutes(times[start_index], times[previous_end])
            # Never merge across a telemetry gap: the dry spell between the two
            # intervals was not observed, so it cannot be called short.
            if (
                separation <= settings.merge_gap_minutes
                and not previous_censored
                and _no_internal_outage(times, previous_end, start_index, settings)
            ):
                merged[-1] = (previous_start, end_index, censored_end)
                continue
        merged.append((start_index, end_index, censored_end))

    intervals = []
    total = len(times)
    for start_index, end_index, censored_end in merged:
        samples = end_index - start_index + 1
        if samples < settings.min_event_samples:
            continue
        start_time, end_time = times[start_index], times[end_index]
        if _minutes(end_time, start_time) < settings.min_event_duration_minutes:
            continue

        gap_before = (
            _minutes(start_time, times[start_index - 1]) if start_index > 0 else None
        )
        gap_after = (
            _minutes(times[end_index + 1], end_time)
            if end_index + 1 < total
            else None
        )
        internal = _interval_minutes(times[start_index:end_index + 1])
        intervals.append(
            DetectedInterval(
                start_time=start_time,
                end_time=end_time,
                samples=int(samples),
                peak_deviation=float(np.max(values[start_index:end_index + 1])),
                start_censored=bool(
                    start_index == 0
                    or (gap_before is not None
                        and gap_before > settings.max_internal_gap_minutes)
                ),
                end_censored=bool(
                    censored_end
                    or end_index == total - 1
                    or (gap_after is not None
                        and gap_after > settings.max_internal_gap_minutes)
                ),
                gap_before_minutes=gap_before,
                gap_after_minutes=gap_after,
                largest_internal_gap_minutes=(
                    float(internal.max()) if internal.size else None
                ),
            )
        )
    return intervals


def _no_internal_outage(times, previous_end, next_start, settings):
    """True when the dry spell between two intervals was fully observed."""
    if next_start <= previous_end + 1:
        return True
    gaps = _interval_minutes(times[previous_end:next_start + 1])
    return bool(gaps.size == 0 or gaps.max() <= settings.max_internal_gap_minutes)


def event_id_for(start_time, event_type=EventType.WETTING):
    """A deterministic identifier for an event.

    Derived from the event type and its normalised local start time, so
    re-running the same analysis over the same data always produces the same
    id. The UTC offset is part of the id because the annual Atlantic fall-back
    repeats an hour of local wall-clock time, and two different events an hour
    apart must not collapse onto one identifier.

    Ids are unique within a station; the station is carried on the event.
    """
    stamp = pd.Timestamp(start_time)
    return f"{event_type}-{stamp.strftime('%Y-%m-%dT%H:%M%z')}"


def detector_metadata(config):
    """The exact detector settings that produced a result."""
    return {
        "method": DETECTION_METHOD,
        "version": WETNESS_DETECTOR_VERSION,
        "sensor": sensors.WETNESS_SIGNAL,
        "wet_direction": config.wet_direction,
        "baseline_window": config.baseline_window,
        "baseline_quantile": config.baseline_quantile,
        "enter_counts": config.enter_counts,
        "exit_counts": config.exit_counts,
        "enter_persistence_samples": config.enter_persistence_samples,
        "exit_persistence_samples": config.exit_persistence_samples,
        "merge_gap_minutes": config.merge_gap_minutes,
        "min_event_samples": config.min_event_samples,
        "min_event_duration_minutes": config.min_event_duration_minutes,
        "max_internal_gap_minutes": config.max_internal_gap_minutes,
    }
