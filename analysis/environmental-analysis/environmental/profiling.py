"""Stage 4: exploratory sensor profiling.

Everything here is a measurement of the *sensor*, not of the environment. The
point is to answer, before any event detection runs, questions like "what does
this channel's baseline actually look like?" and "are these zeros observations
or sentinels?" -- so that the detector is built on what the signal is rather
than on what it was assumed to be.

Profiles are structured data. Plots are optional and live in :mod:`.plots`.
"""

import numpy as np
import pandas as pd

from . import sensors
from .baselines import diurnal_profile, wetness_deviation
from .models import DataQuality, SensorProfile
from .quality import gap_segments
from .statistics import describe, longest_run


def profile_sensor(dataset, sensor):
    """Profile one canonical sensor."""
    semantics = sensors.SEMANTICS[sensor]
    raw = dataset.series(sensor, valid_only=False)
    values = dataset.series(sensor)
    present = values.dropna()

    findings = {
        "present_readings": int(raw.notna().sum()),
        "masked_as_invalid": int((raw.notna() & values.isna()).sum()),
        "zero_readings": int((raw == 0).sum()),
        "distinct_values": int(present.nunique()),
    }
    notes = []
    if findings["masked_as_invalid"]:
        notes.append(
            f"{findings['masked_as_invalid']} present reading(s) were masked as "
            "physically implausible, corrupt, or an ambiguous zero"
        )

    if sensor in (sensors.WETNESS_SIGNAL, sensors.SOIL_SIGNAL):
        findings.update(_adc_findings(present))
    if sensor == sensors.WETNESS_SIGNAL:
        findings.update(_wetness_findings(dataset))
    if sensor == sensors.SOIL_SIGNAL:
        findings.update(_soil_findings(dataset, raw, present))
    if sensor in (sensors.TEMPERATURE, sensors.HUMIDITY):
        findings.update(_rate_findings(dataset, sensor))
        findings["diurnal"] = {
            hour: point.to_dict()
            for hour, point in diurnal_profile(values, min_samples=20).items()
        }
    if sensor == sensors.PRESSURE:
        findings.update(_pressure_findings(dataset, raw))

    quality = _profile_quality(dataset, sensor)
    return SensorProfile(
        sensor=sensor,
        unit=semantics.unit,
        calibrated=semantics.calibrated,
        observations=len(dataset),
        valid_observations=int(present.size),
        valid_fraction=dataset.valid_fraction(sensor),
        statistics=describe(present),
        quality=quality,
        findings=findings,
        notes=tuple(notes) + (semantics.means,),
    )


def profile_environment(dataset):
    """Profile every sensor present in the dataset."""
    return {
        sensor: profile_sensor(dataset, sensor)
        for sensor in dataset.sensor_columns
    }


# --------------------------------------------------------------------------


def _profile_quality(dataset, sensor):
    from .quality import assess_window

    return assess_window(
        dataset, sensor, dataset.start_time, dataset.end_time, min_samples=1
    )


def _adc_findings(present):
    if present.empty:
        return {}
    counts = present.value_counts()
    top = counts.index[0]
    return {
        "mode_value": float(top),
        "mode_fraction": float(counts.iloc[0] / counts.sum()),
        "at_upper_rail_fraction": float((present >= 4095).mean()),
        "at_lower_rail_fraction": float((present <= 0).mean()),
        "step_change_p99": (
            float(present.diff().abs().dropna().quantile(0.99))
            if len(present) > 1
            else None
        ),
    }


def _wetness_findings(dataset):
    """Findings that decide how the wetness detector must be built."""
    frame = wetness_deviation(dataset)
    deviation = frame["deviation"].dropna()
    reference = frame["reference"].dropna()
    if deviation.empty:
        return {}

    dry = deviation <= 0
    dry_steps = frame["signal"].dropna().diff().abs()[dry.reindex(
        frame["signal"].dropna().index, fill_value=False
    )]
    hours = deviation.index.hour
    wet = deviation > 0
    by_hour = (
        pd.Series(wet.to_numpy(), index=hours).groupby(level=0).mean().round(4)
    )
    return {
        "dry_reference_median": float(reference.median()),
        "dry_reference_min": float(reference.min()),
        "dry_reference_max": float(reference.max()),
        "dry_fraction": float(dry.mean()),
        "deviation_percentiles": {
            str(q): float(deviation.quantile(q / 100.0))
            for q in (50, 75, 90, 95, 99)
        },
        # The number that rules out a robust z-score: if consecutive dry-state
        # differences are overwhelmingly zero, the dry-state MAD is zero too.
        "dry_state_zero_step_fraction": (
            float((dry_steps == 0).mean()) if len(dry_steps) else None
        ),
        "wet_fraction_by_local_hour": {int(k): float(v) for k, v in by_hour.items()},
    }


def _soil_findings(dataset, raw, present):
    """Findings that decide whether the soil channel may be interpreted."""
    findings = {}
    valid_index = present.index
    if len(valid_index) > 1:
        intervals = (
            valid_index.to_series().diff().dropna().dt.total_seconds() / 60.0
        )
        findings["sampling_interval_minutes"] = {
            "median": float(intervals.median()),
            "p90": float(intervals.quantile(0.90)),
        }
    findings["zero_fraction_of_present"] = (
        float((raw == 0).sum() / raw.notna().sum()) if raw.notna().any() else None
    )
    findings["zero_interpretation"] = (
        "ambiguous: scripts/apps_script/doGet.js blanks zero values before "
        "writing, so a stored zero predates that behaviour and cannot be told "
        "apart from a missing-value sentinel. Zeros are excluded from analysis."
    )
    if not present.empty:
        flat = present.diff() == 0
        run_length, _ = longest_run(flat.to_numpy())
        findings["longest_unchanged_run_samples"] = int(run_length)
        by_month = present.resample("ME").agg(["count", "median", "std"])
        findings["monthly"] = {
            str(period.date()): {
                "valid": int(row["count"]),
                "median": None if pd.isna(row["median"]) else float(row["median"]),
                "std": None if pd.isna(row["std"]) else float(row["std"]),
            }
            for period, row in by_month.iterrows()
        }
        temperature = dataset.series(sensors.TEMPERATURE)
        aligned = pd.concat([present, temperature], axis=1, sort=False).dropna()
        if len(aligned) > 30:
            soil_ranks = aligned.iloc[:, 0].rank()
            temperature_ranks = aligned.iloc[:, 1].rank()
            # A constant channel has no rank variance, and a correlation
            # against it is undefined rather than zero.
            if soil_ranks.nunique() > 1 and temperature_ranks.nunique() > 1:
                findings["spearman_with_temperature"] = float(
                    soil_ranks.corr(temperature_ranks)
                )
        profile = diurnal_profile(present, min_samples=20)
        if profile:
            medians = [point.median for point in profile.values()]
            findings["diurnal_swing_counts"] = float(max(medians) - min(medians))
    return findings


def _rate_findings(dataset, sensor):
    values = dataset.series(sensor).dropna()
    if len(values) < 2:
        return {}
    minutes = values.index.to_series().diff().dt.total_seconds() / 60.0
    usable = minutes >= dataset.config.quality.min_inter_arrival_minutes
    usable &= minutes <= dataset.config.quality.continuity_gap_minutes
    rates = (values.diff() / minutes)[usable].dropna()
    if rates.empty:
        return {}
    return {
        "rate_per_minute": {
            "median_abs": float(rates.abs().median()),
            "p99_abs": float(rates.abs().quantile(0.99)),
            "max_abs": float(rates.abs().max()),
        }
    }


def _pressure_findings(dataset, raw):
    low, high = 800.0, 1100.0
    present = raw.dropna()
    if present.empty:
        return {}
    impossible = present[(present < low) | (present > high)]
    findings = {
        "impossible_readings": int(impossible.size),
        "impossible_value_counts": {
            f"{value:g}": int(count)
            for value, count in impossible.value_counts().head(5).items()
        },
    }
    if impossible.size:
        findings["impossible_period"] = {
            "first": impossible.index[0].isoformat(),
            "last": impossible.index[-1].isoformat(),
        }
        local = impossible.index.tz_localize(None).to_period("M")
        by_month = impossible.groupby(local).size()
        findings["impossible_by_month"] = {
            str(period): int(count) for period, count in by_month.items()
        }
    return findings


def telemetry_profile(dataset):
    """A profile of telemetry delivery itself, as context for every other one."""
    segments = gap_segments(dataset)
    coverage = dataset.coverage()
    return {
        "rows": len(dataset),
        "start_time": dataset.start_time.isoformat() if dataset.start_time else None,
        "end_time": dataset.end_time.isoformat() if dataset.end_time else None,
        "continuous_segments": len(segments),
        "longest_segment_hours": (
            round(
                max(
                    ((end - start).total_seconds() / 3600.0 for start, end in segments),
                    default=0.0,
                ),
                2,
            )
        ),
        "coverage_fraction": coverage["fraction"],
        "expected_transmissions": coverage["expected"],
        "sampling": dataset.sampling_statistics(),
        "note": (
            "coverage is measured against the reliability audit's operating "
            "schedule; missing telemetry is never interpreted as environmental "
            "behaviour"
        ),
    }
