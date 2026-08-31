"""Baselines: the reference levels every deviation is measured against.

Two different kinds of baseline live here.

**The wetness dry reference** is the level the wetness channel sits at when the
sensing surface is dry. It is estimated as an upper quantile of a trailing and
leading window rather than a median, because the dry state is a *ceiling*: the
signal only ever departs downward from it. A rolling median would sag during a
multi-day wet spell and hide the very event it is supposed to measure, while an
upper quantile stays on the dry level as long as roughly a tenth of the window
is dry -- and still adapts if the rail itself drifts, which it does in this
deployment (the observed dry level falls from 4095 to about 4062-4074 in late
July 2026).

**Environmental baselines** are the station's own climatology: what a normal
May looks like here, what the diurnal temperature curve is, how often wetting
events occur. They are computed with coverage attached, so a month with a tenth
of its telemetry is never presented as though it carried the same weight as a
complete one.
"""

import numpy as np
import pandas as pd

from . import sensors
from .models import (
    DiurnalPoint,
    EnvironmentalBaseline,
    EvidenceStrength,
    PeriodBaseline,
)
from .quality import dataset_quality
from .statistics import describe
from .version import BASELINE_VERSION


# --------------------------------------------------------------------------
# Wetness dry reference
# --------------------------------------------------------------------------


def dry_reference(signal, config):
    """Estimate the dry reference level of the wetness channel over time.

    ``signal`` must be a Series of valid readings indexed by timestamp. The
    window is centred: this is an offline analysis over a fixed record, and a
    symmetric window gives a stabler reference at the start and end of a wet
    spell than a trailing one. Windows without enough observations fall back to
    a single global quantile so that a sparse stretch still gets a reference
    rather than silently producing no events.
    """
    present = signal.dropna()
    if present.empty:
        return pd.Series(dtype="float64", index=signal.index)

    # Which tail of the window is "dry" depends on the sensor's polarity. With
    # the observed convention (lower counts mean wetter) the dry state is the
    # upper tail; an inverted board would put it in the lower tail instead.
    quantile = (
        config.baseline_quantile
        if config.wet_direction < 0
        else 1.0 - config.baseline_quantile
    )
    global_quantile = (
        config.global_baseline_quantile
        if config.wet_direction < 0
        else 1.0 - config.global_baseline_quantile
    )
    rolling = present.rolling(
        config.baseline_window,
        center=True,
        min_periods=config.baseline_min_samples,
    ).quantile(quantile)

    fallback = float(present.quantile(global_quantile))
    rolling = rolling.fillna(fallback)
    return rolling.reindex(signal.index)


def wetness_deviation(dataset, config=None):
    """Return the wetness signal, its dry reference and the deviation from it.

    The deviation is expressed as "counts away from dry in the wet direction",
    so it is positive when wet regardless of the sensor's sign convention.
    """
    config = config or dataset.config.wetness
    signal = dataset.series(sensors.WETNESS_SIGNAL)
    reference = dry_reference(signal, config)
    if config.wet_direction < 0:
        deviation = reference - signal      # lower counts mean wetter
    else:
        deviation = signal - reference      # higher counts mean wetter
    deviation = deviation.clip(lower=0.0)
    return pd.DataFrame(
        {"signal": signal, "reference": reference, "deviation": deviation}
    )


# --------------------------------------------------------------------------
# Diurnal profiles
# --------------------------------------------------------------------------


def diurnal_profile(series, min_samples=20):
    """Median, p10 and p90 of a signal for each local hour of the day."""
    present = series.dropna()
    if present.empty:
        return {}
    grouped = present.groupby(present.index.hour)
    points = {}
    for hour, values in grouped:
        if len(values) < min_samples:
            continue
        points[int(hour)] = DiurnalPoint(
            hour=int(hour),
            samples=int(len(values)),
            median=float(values.median()),
            p10=float(values.quantile(0.10)),
            p90=float(values.quantile(0.90)),
        )
    return points


def rolling_hour_of_day_median(series, window_days=7.0, min_samples=3):
    """A slowly varying time-of-day profile, evaluated at each observation.

    Used to remove a sensor's daily cycle before testing it for a response.
    For each observation the value returned is the median of observations in
    the same local hour within +/- ``window_days``. Returns NaN where the
    profile cannot be estimated, so callers can fall back rather than subtract
    a fabricated number.
    """
    present = series.dropna()
    if present.empty:
        return pd.Series(dtype="float64", index=series.index)

    half_window = pd.Timedelta(days=window_days / 2.0)
    profile = pd.Series(np.nan, index=present.index, dtype="float64")
    hours = present.index.hour
    times = present.index
    for hour in np.unique(hours):
        mask = hours == hour
        same_hour = present[mask]
        if len(same_hour) < min_samples:
            continue
        hour_times = same_hour.index
        values = same_hour.to_numpy()
        # Vectorised window bounds: same-hour observations are few (at most a
        # dozen per day), so a searchsorted pair per hour is inexpensive.
        lower = np.searchsorted(hour_times, hour_times - half_window, side="left")
        upper = np.searchsorted(hour_times, hour_times + half_window, side="right")
        medians = np.full(len(values), np.nan)
        for position in range(len(values)):
            window = values[lower[position]:upper[position]]
            if window.size >= min_samples:
                medians[position] = np.median(window)
        profile.loc[hour_times] = medians
    return profile.reindex(series.index)


# --------------------------------------------------------------------------
# Environmental baselines
# --------------------------------------------------------------------------


_BASELINE_SENSORS = (
    sensors.TEMPERATURE,
    sensors.HUMIDITY,
    sensors.PRESSURE,
    sensors.SOIL_SIGNAL,
    sensors.WETNESS_SIGNAL,
)


def _evidence_strength_for_coverage(fraction, config):
    if fraction is None:
        return EvidenceStrength.INSUFFICIENT
    if fraction >= config.strong_coverage_fraction:
        return EvidenceStrength.STRONG
    if fraction >= config.usable_coverage_fraction:
        return EvidenceStrength.MODERATE
    return EvidenceStrength.WEAK


def build_environmental_baseline(dataset, events=(), config=None):
    """Characterise the station's own environmental normals.

    Every period carries its telemetry coverage and an explicit evidence
    strength. A month with 10% coverage is reported, because hiding it would be
    worse, but it is never labelled as strong evidence.
    """
    settings = config or dataset.config
    baseline_config = settings.baseline

    periods = []
    if len(dataset):
        # Periods are local calendar months, so the zone is dropped for the
        # grouping key only; every timestamp kept on a result stays aware.
        stamps = dataset.timestamps.tz_localize(None).to_period(baseline_config.period)
        for period in stamps.unique().sort_values():
            mask = stamps == period
            window = dataset.frame.index[mask]
            start, end = window[0], window[-1]
            subset = dataset.subset(start, end)
            coverage = subset.coverage(
                period.start_time.tz_localize(dataset.timestamps.tz),
                min(
                    period.end_time.tz_localize(dataset.timestamps.tz),
                    dataset.end_time,
                ),
            )
            fraction = coverage["fraction"]
            per_sensor = {}
            for sensor in _BASELINE_SENSORS:
                values = subset.series(sensor).dropna()
                if len(values) < baseline_config.min_period_samples:
                    continue
                per_sensor[sensor] = describe(values)
            period_events = [
                event for event in events
                if start <= event.start_time <= end
            ]
            periods.append(
                PeriodBaseline(
                    period=str(period),
                    start_time=start,
                    end_time=end,
                    observations=len(subset),
                    expected_observations=coverage["expected"],
                    telemetry_coverage=fraction,
                    evidence_strength=_evidence_strength_for_coverage(
                        fraction, baseline_config
                    ),
                    sensors=per_sensor,
                    wetness_event_count=len(period_events),
                    wetness_event_hours=round(
                        sum(e.duration_minutes for e in period_events) / 60.0, 2
                    ),
                    notes=(
                        (
                            "sensor statistics omitted where fewer than "
                            f"{baseline_config.min_period_samples} valid "
                            "observations were available",
                        )
                        if len(per_sensor) < len(_BASELINE_SENSORS)
                        else ()
                    ),
                )
            )

    diurnal = {}
    for sensor in _BASELINE_SENSORS:
        profile = diurnal_profile(
            dataset.series(sensor), min_samples=baseline_config.min_diurnal_samples
        )
        if profile:
            diurnal[sensor] = profile

    daily_ranges = {}
    for sensor in (sensors.TEMPERATURE, sensors.HUMIDITY):
        values = dataset.series(sensor).dropna()
        if values.empty:
            continue
        by_day = values.resample("D")
        counts = by_day.count()
        # A day with a handful of readings has no meaningful daily range.
        usable = counts >= baseline_config.min_diurnal_samples
        spans = (by_day.max() - by_day.min())[usable]
        if spans.empty:
            continue
        daily_ranges[sensor] = {
            "days": int(len(spans)),
            "statistics": describe(spans),
            "note": (
                f"days with fewer than {baseline_config.min_diurnal_samples} "
                "valid observations are excluded"
            ),
        }

    durations = [event.duration_minutes for event in events]
    wetness_events = {
        "count": len(events),
        "per_day": (
            round(len(events) / max(1e-9, _record_days(dataset)), 3)
            if len(dataset)
            else None
        ),
        "duration_minutes": describe(durations) if durations else None,
        "note": (
            "event frequency is per day of record, not per day of telemetry; "
            "telemetry coverage over the record is "
            + (
                f"{dataset.coverage()['fraction']:.0%}"
                if dataset.coverage()["fraction"] is not None
                else "unknown"
            )
        ),
    }

    return EnvironmentalBaseline(
        station_id=settings.station_id,
        start_time=dataset.start_time,
        end_time=dataset.end_time,
        periods=tuple(periods),
        diurnal=diurnal,
        daily_ranges=daily_ranges,
        wetness_events=wetness_events,
        quality=dataset_quality(dataset),
        version=BASELINE_VERSION,
    )


def _record_days(dataset):
    if dataset.start_time is None:
        return 0.0
    return max(
        1e-9, (dataset.end_time - dataset.start_time).total_seconds() / 86400.0
    )
