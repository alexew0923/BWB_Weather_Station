"""Stage 10: environmental anomalies.

An anomaly here is a statement about the *environment* being unusual compared
with what this station has recorded in comparable conditions. Sensor faults,
stuck channels, missing telemetry and delivery outages are explicitly not
anomalies: those belong to the sensor-health analysis and the reliability
audit, and mixing them in would make an instrument failure look like weather.

Method
------

For every candidate observation the engine builds a **conditional baseline**:
observations from the same local hour of day (plus or minus ``hour_window``)
and from a comparable point in the year (plus or minus ``day_of_year_window``
days). An observation is anomalous only when it fails *both* of two independent
tests -- a robust z-score past ``robust_z_threshold`` and a position outside the
1st/99th percentile envelope -- against a baseline with at least
``min_history_samples`` comparable observations. Requiring both keeps a tight
but noisy distribution from producing a constant stream of "anomalies".

Consecutive anomalous observations are collapsed into one anomaly, represented
by its most extreme sample, so a six-hour heat spike is one finding rather than
seventy.

The honest limitation, stated on every result: this record covers a single
partial year, so the "seasonal" baseline is really a local window inside one
deployment. It answers "unusual for this station around this time of year in
this record", not "unusual for Nova Scotia".
"""

import numpy as np
import pandas as pd

from . import sensors
from .models import (
    AnomalyKind,
    DataQuality,
    EnvironmentalAnomaly,
    Evidence,
    EvidenceStrength,
    Interpretation,
    QualityAssessment,
    StatementKind,
)
from .statistics import MAD_TO_SIGMA
from .version import ANOMALY_DETECTOR_VERSION

_HIGH_LOW = {
    sensors.TEMPERATURE: (AnomalyKind.HIGH_TEMPERATURE, AnomalyKind.LOW_TEMPERATURE),
    sensors.HUMIDITY: (AnomalyKind.HIGH_HUMIDITY, AnomalyKind.LOW_HUMIDITY),
    sensors.SOIL_SIGNAL: (
        AnomalyKind.UNUSUAL_SOIL_SIGNAL,
        AnomalyKind.UNUSUAL_SOIL_SIGNAL,
    ),
}

BASELINE_CAVEAT = (
    "the comparison baseline is this station's own record, which covers a "
    "single partial year, so this is 'unusual for this deployment', not "
    "'unusual for the region'"
)


def _conditional_statistics(values, times, config, sigma_floor):
    """Median, scale and percentile envelope for each observation's peers.

    Computed once per (local hour, local date) bucket rather than once per
    observation: every observation inside one bucket has the same peer group,
    and there are a few thousand buckets against tens of thousands of rows.
    """
    day_of_year = times.dayofyear.to_numpy()
    hour = times.hour.to_numpy()
    order = np.arange(values.size)

    median = np.full(values.size, np.nan)
    scale = np.full(values.size, np.nan)
    low = np.full(values.size, np.nan)
    high = np.full(values.size, np.nan)
    support = np.zeros(values.size, dtype="int64")

    for target_hour in np.unique(hour):
        hour_distance = np.abs(hour - target_hour)
        # Hours wrap around midnight.
        hour_distance = np.minimum(hour_distance, 24 - hour_distance)
        peers = hour_distance <= config.hour_window
        peer_days = day_of_year[peers]
        peer_values = values[peers]
        if peer_values.size == 0:
            continue
        sort = np.argsort(peer_days, kind="stable")
        peer_days = peer_days[sort]
        peer_values = peer_values[sort]

        targets = order[hour == target_hour]
        for day in np.unique(day_of_year[hour == target_hour]):
            lower = np.searchsorted(peer_days, day - config.day_of_year_window, "left")
            upper = np.searchsorted(peer_days, day + config.day_of_year_window, "right")
            if config.exclude_same_day:
                # Two slices either side of the target day, so an excursion is
                # never part of the distribution it is being judged against.
                same_low = np.searchsorted(peer_days, day, "left")
                same_high = np.searchsorted(peer_days, day, "right")
                window = np.concatenate(
                    (peer_values[lower:same_low], peer_values[same_high:upper])
                )
            else:
                window = peer_values[lower:upper]
            if window.size < config.min_history_samples:
                continue
            centre = float(np.median(window))
            mad = float(np.median(np.abs(window - centre)))
            positions = targets[day_of_year[targets] == day]
            median[positions] = centre
            scale[positions] = max(mad * MAD_TO_SIGMA, sigma_floor)
            low[positions] = np.percentile(window, config.low_percentile)
            high[positions] = np.percentile(window, config.high_percentile)
            support[positions] = window.size
    return median, scale, low, high, support


def _collapse_runs(flags, times, magnitude, merge_gap_minutes=0.0):
    """Group flagged samples into anomalies, returning the extreme of each.

    Runs separated by less than ``merge_gap_minutes`` are joined. An extreme
    excursion is part of its own comparison window, so a long excursion can
    push its own percentile envelope outwards and leave unflagged samples in
    the middle of what is plainly one episode. Merging closes those holes
    instead of reporting one heat spike as ten findings.
    """
    runs = []
    start = None
    for index, flag in enumerate(flags):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            runs.append((start, index - 1))
            start = None
    if start is not None:
        runs.append((start, flags.size - 1))

    if merge_gap_minutes:
        merged = []
        for first, last in runs:
            if merged:
                separation = (
                    times[first] - times[merged[-1][1]]
                ).total_seconds() / 60.0
                if separation <= merge_gap_minutes:
                    merged[-1] = (merged[-1][0], last)
                    continue
            merged.append((first, last))
        runs = merged

    collapsed = []
    for first, last in runs:
        segment = magnitude[first:last + 1]
        pick = first + int(np.argmax(np.abs(segment)))
        collapsed.append((pick, first, last))
    return collapsed


def detect_value_anomalies(dataset, sensor, config=None):
    """Anomalies in a sensor's level, against its conditional baseline."""
    settings = config or dataset.config.anomaly
    series = dataset.series(sensor).dropna()
    if series.size < settings.min_history_samples:
        return []

    values = series.to_numpy(dtype="float64")
    times = series.index
    floor = settings.min_sigma.get(sensor, 0.0)
    median, scale, low, high, support = _conditional_statistics(
        values, times, settings, floor
    )

    with np.errstate(invalid="ignore", divide="ignore"):
        z = (values - median) / scale
    outside = (values < low) | (values > high)
    flagged = np.isfinite(z) & (np.abs(z) >= settings.robust_z_threshold) & outside

    high_kind, low_kind = _HIGH_LOW.get(
        sensor, (AnomalyKind.UNUSUAL_SOIL_SIGNAL, AnomalyKind.UNUSUAL_SOIL_SIGNAL)
    )
    semantics = sensors.SEMANTICS[sensor]

    anomalies = []
    for pick, first, last in _collapse_runs(
        flagged, times, np.nan_to_num(z), settings.merge_gap_minutes
    ):
        moment = times[pick]
        value = float(values[pick])
        percentile = float(
            100.0 * np.mean(values[np.isfinite(values)] <= value)
        )
        kind = high_kind if z[pick] > 0 else low_kind
        run_minutes = (times[last] - times[first]).total_seconds() / 60.0
        anomalies.append(
            EnvironmentalAnomaly(
                anomaly_id=f"{kind}-{moment.strftime('%Y-%m-%dT%H:%M%z')}",
                kind=kind,
                sensor=sensor,
                time=moment,
                value=value,
                baseline_median=float(median[pick]),
                robust_z=float(z[pick]),
                percentile=percentile,
                history_samples=int(support[pick]),
                evidence_strength=(
                    EvidenceStrength.STRONG
                    if support[pick] >= 4 * settings.min_history_samples
                    else EvidenceStrength.MODERATE
                ),
                quality=QualityAssessment(
                    level=DataQuality.USABLE,
                    reasons=(),
                    observations=int(last - first + 1),
                    valid_observations=int(last - first + 1),
                ),
                evidence=(
                    Evidence(
                        StatementKind.RAW_OBSERVATION,
                        f"{sensor} read {value:.2f} {semantics.unit} at "
                        f"{moment.isoformat()}.",
                        quantity=sensor,
                        value=value,
                        unit=semantics.unit,
                    ),
                    Evidence(
                        StatementKind.STATISTICAL_EVIDENCE,
                        f"Comparable observations (same local hour +/-"
                        f"{settings.hour_window}h, same time of year +/-"
                        f"{settings.day_of_year_window}d, n="
                        f"{int(support[pick])}) have a median of "
                        f"{median[pick]:.2f}; this reading is "
                        f"{z[pick]:+.1f} robust standard deviations from it and "
                        f"outside the {settings.low_percentile:g}-"
                        f"{settings.high_percentile:g} percentile envelope.",
                        quantity="robust_z",
                        value=float(z[pick]),
                    ),
                    Evidence(
                        StatementKind.COMPUTED_MEASUREMENT,
                        f"The condition persisted across {int(last - first + 1)} "
                        f"consecutive observations spanning {run_minutes:.0f} "
                        "minutes.",
                        quantity="run_minutes",
                        value=run_minutes,
                        unit="minutes",
                    ),
                ),
                interpretation=Interpretation(
                    statement=(
                        f"{sensor} was unusually "
                        f"{'high' if z[pick] > 0 else 'low'} for this time of "
                        "year and time of day."
                    ),
                    evidence_strength=EvidenceStrength.MODERATE,
                    caveats=(BASELINE_CAVEAT,),
                ),
                version=ANOMALY_DETECTOR_VERSION,
            )
        )
    return anomalies


def detect_rate_anomalies(dataset, sensor, config=None):
    """Anomalously fast change in a sensor, judged against its own history."""
    settings = config or dataset.config.anomaly
    series = dataset.series(sensor).dropna()
    if series.size < settings.rate_min_history_samples:
        return []

    minutes = series.index.to_series().diff().dt.total_seconds() / 60.0
    usable = (
        (minutes >= dataset.config.quality.min_inter_arrival_minutes)
        & (minutes <= dataset.config.quality.continuity_gap_minutes)
    )
    rates = (series.diff() / minutes)[usable].dropna()
    if rates.size < settings.rate_min_history_samples:
        return []

    values = rates.to_numpy(dtype="float64")
    centre = float(np.median(values))
    mad = float(np.median(np.abs(values - centre)))
    scale = max(mad * MAD_TO_SIGMA, settings.rate_min_sigma.get(sensor, 1e-6))
    z = (values - centre) / scale
    magnitude = np.abs(values - centre)
    extreme = float(np.percentile(magnitude, settings.rate_extreme_percentile))
    # Two independent gates, as for value anomalies: a robust z past the
    # threshold AND membership of the extreme tail of all observed rates.
    flagged = (np.abs(z) >= settings.rate_robust_z_threshold) & (magnitude >= extreme)

    semantics = sensors.SEMANTICS[sensor]
    anomalies = []
    for pick, first, last in _collapse_runs(
        flagged, rates.index, z, settings.merge_gap_minutes
    ):
        moment = rates.index[pick]
        anomalies.append(
            EnvironmentalAnomaly(
                anomaly_id=(
                    f"{AnomalyKind.RAPID_TEMPERATURE_CHANGE}-"
                    f"{moment.strftime('%Y-%m-%dT%H:%M%z')}"
                ),
                kind=AnomalyKind.RAPID_TEMPERATURE_CHANGE,
                sensor=sensor,
                time=moment,
                value=float(values[pick]),
                baseline_median=centre,
                robust_z=float(z[pick]),
                percentile=float(100.0 * np.mean(values <= values[pick])),
                history_samples=int(values.size),
                evidence_strength=EvidenceStrength.MODERATE,
                quality=QualityAssessment(
                    level=DataQuality.USABLE,
                    observations=int(values.size),
                    valid_observations=int(values.size),
                ),
                evidence=(
                    Evidence(
                        StatementKind.COMPUTED_MEASUREMENT,
                        f"{sensor} changed at {values[pick]:+.3f} "
                        f"{semantics.unit} per minute at {moment.isoformat()}.",
                        quantity=f"{sensor}_rate",
                        value=float(values[pick]),
                        unit=f"{semantics.unit}/minute",
                    ),
                    Evidence(
                        StatementKind.STATISTICAL_EVIDENCE,
                        f"That is {z[pick]:+.1f} robust standard deviations from "
                        f"the median rate over {values.size} observed intervals, "
                        f"and in the top "
                        f"{100 - settings.rate_extreme_percentile:g}% of all "
                        "observed rates of change.",
                        quantity="robust_z",
                        value=float(z[pick]),
                    ),
                ),
                interpretation=Interpretation(
                    statement=(
                        f"{sensor} changed unusually quickly compared with the "
                        "rest of the record."
                    ),
                    evidence_strength=EvidenceStrength.MODERATE,
                    caveats=(
                        BASELINE_CAVEAT,
                        "a fast change is not by itself evidence of a sensor "
                        "fault; sensor health is analysed separately",
                    ),
                ),
                version=ANOMALY_DETECTOR_VERSION,
            )
        )
    return anomalies


def detect_persistence_anomalies(events, config=None, quality_config=None):
    """Wetting events that lasted unusually long for this record."""
    settings = config
    if settings is None:
        raise ValueError("an AnomalyConfig is required")
    durations = np.array([event.duration_minutes for event in events], dtype="float64")
    if durations.size < 10:
        return []
    cutoff = float(np.percentile(durations, settings.persistence_percentile))
    anomalies = []
    for event in events:
        if event.duration_minutes < cutoff or event.boundaries.start_censored:
            continue
        anomalies.append(
            EnvironmentalAnomaly(
                anomaly_id=(
                    f"{AnomalyKind.PERSISTENT_WETNESS}-"
                    f"{event.start_time.strftime('%Y-%m-%dT%H:%M%z')}"
                ),
                kind=AnomalyKind.PERSISTENT_WETNESS,
                sensor=sensors.WETNESS_SIGNAL,
                time=event.start_time,
                value=event.duration_minutes,
                baseline_median=float(np.median(durations)),
                robust_z=None,
                percentile=float(100.0 * np.mean(durations <= event.duration_minutes)),
                history_samples=int(durations.size),
                evidence_strength=EvidenceStrength.MODERATE,
                quality=event.data_quality,
                evidence=(
                    Evidence(
                        StatementKind.COMPUTED_MEASUREMENT,
                        f"The wetness signal stayed away from its dry reference "
                        f"for {event.duration_minutes:.0f} minutes.",
                        quantity="duration_minutes",
                        value=event.duration_minutes,
                        unit="minutes",
                    ),
                    Evidence(
                        StatementKind.STATISTICAL_EVIDENCE,
                        f"That exceeds the "
                        f"{settings.persistence_percentile:g}th percentile "
                        f"({cutoff:.0f} minutes) of the {durations.size} events "
                        "detected in this record.",
                        quantity="duration_percentile_cutoff",
                        value=cutoff,
                        unit="minutes",
                    ),
                ),
                interpretation=Interpretation(
                    statement=(
                        "The surface stayed wet unusually long compared with "
                        "other events in this record."
                    ),
                    evidence_strength=EvidenceStrength.MODERATE,
                    caveats=(
                        BASELINE_CAVEAT,
                        "a long wetness signal may reflect slow drying of the "
                        "board rather than continued precipitation",
                    ),
                ),
                version=ANOMALY_DETECTOR_VERSION,
            )
        )
    return anomalies


def detect_anomalies(dataset, events=(), config=None):
    """Run every V1 anomaly detector and return findings in time order."""
    settings = config or dataset.config.anomaly
    found = []
    for sensor in (sensors.TEMPERATURE, sensors.HUMIDITY):
        found.extend(detect_value_anomalies(dataset, sensor, settings))
    found.extend(detect_rate_anomalies(dataset, sensors.TEMPERATURE, settings))
    found.extend(detect_persistence_anomalies(events, settings))
    found.sort(key=lambda item: (item.time, item.anomaly_id))
    return found
