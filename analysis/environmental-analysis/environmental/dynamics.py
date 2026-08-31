"""Stage 8: post-event dynamics.

Descriptive first, models second, and never mechanistic.

The descriptive measures -- peak, time to peak, change from peak, fraction
recovered, median rate of change -- are arithmetic on observations and are
always computed when the data supports them.

Model fitting is opt-in (``PostEventDynamicsConfig.fit_models``) and produces
*empirical* fits. An exponential relaxation is offered because it is the
conventional shape for a draining medium, not because anything here shows that
this soil drains exponentially. Both candidate models are fitted, both sets of
residual metrics are exposed, and neither is presented as the physical truth.

Fitting uses NumPy only. The exponential is solved by a deterministic grid
search over the rate constant with a linear least-squares solve for the two
remaining parameters at each grid point, which keeps the result bit-identical
between runs and keeps SciPy out of the dependency list.
"""

import numpy as np
import pandas as pd

from . import sensors
from .models import DataQuality, ModelFit, PostEventDynamics, QualityAssessment
from .quality import assess_window
from .version import POST_EVENT_DYNAMICS_VERSION


def analyze_post_event_dynamics(dataset, interval, soil_response, context=None,
                                config=None):
    """Characterise how the soil signal behaved after a wetting event."""
    settings = config or dataset.config.dynamics
    series = (
        context.residual if context is not None
        else dataset.series(sensors.SOIL_SIGNAL)
    )

    window_end = interval.end_time + pd.Timedelta(hours=settings.window_hours)
    quality = assess_window(
        dataset,
        sensors.SOIL_SIGNAL,
        interval.start_time,
        window_end,
        min_samples=settings.min_samples,
    )
    values = series.loc[interval.start_time:window_end].dropna()

    if quality.level is not DataQuality.USABLE or len(values) < settings.min_samples:
        return PostEventDynamics(
            quality=QualityAssessment(
                level=DataQuality.INSUFFICIENT
                if quality.level is DataQuality.USABLE
                else quality.level,
                reasons=quality.reasons
                + (
                    f"{len(values)} valid soil observation(s) in the "
                    f"{settings.window_hours:g}-hour post-event window, "
                    f"{settings.min_samples} required",
                ),
                observations=quality.observations,
                valid_observations=len(values),
                expected_observations=quality.expected_observations,
                telemetry_coverage=quality.telemetry_coverage,
                longest_gap_minutes=quality.longest_gap_minutes,
            ),
            samples=len(values),
            version=POST_EVENT_DYNAMICS_VERSION,
        )

    baseline = soil_response.baseline_counts
    deviations = values - baseline if baseline is not None else values - values.iloc[0]
    peak_position = deviations.abs().idxmax()
    peak_value = float(values.loc[peak_position])
    peak_deviation = float(deviations.loc[peak_position])
    end_value = float(values.iloc[-1])
    change_from_peak = end_value - peak_value

    fraction_recovered = None
    half_recovery = None
    if baseline is not None and abs(peak_deviation) > 1e-9:
        fraction_recovered = float(
            1.0 - (end_value - baseline) / peak_deviation
        )
        after_peak = values.loc[peak_position:]
        target = baseline + peak_deviation / 2.0
        crossed = (
            after_peak <= target if peak_deviation > 0 else after_peak >= target
        )
        if crossed.any():
            half_recovery = float(
                (crossed.idxmax() - peak_position).total_seconds() / 60.0
            )

    hours = (values.index - values.index[0]).total_seconds().to_numpy() / 3600.0
    rates = np.diff(values.to_numpy()) / np.maximum(np.diff(hours), 1e-9)
    median_rate = float(np.median(rates)) if rates.size else None

    returned = None
    if baseline is not None and soil_response.detection_threshold_counts:
        returned = bool(
            abs(end_value - baseline) < soil_response.detection_threshold_counts
        )

    models = ()
    if settings.fit_models and len(values) >= settings.min_fit_samples:
        after_peak = values.loc[peak_position:]
        if len(after_peak) >= settings.min_fit_samples:
            models = _fit_models(after_peak, settings)

    return PostEventDynamics(
        quality=quality,
        samples=len(values),
        window_minutes=float(
            (values.index[-1] - values.index[0]).total_seconds() / 60.0
        ),
        peak_counts=peak_value,
        time_to_peak_minutes=float(
            (peak_position - interval.start_time).total_seconds() / 60.0
        ),
        end_counts=end_value,
        change_from_peak_counts=change_from_peak,
        fraction_recovered=fraction_recovered,
        time_to_half_recovery_minutes=half_recovery,
        median_rate_counts_per_hour=median_rate,
        returned_to_baseline=returned,
        models=models,
        version=POST_EVENT_DYNAMICS_VERSION,
    )


# --------------------------------------------------------------------------
# Empirical model fitting
# --------------------------------------------------------------------------


def _metrics(observed, predicted, parameter_count):
    residuals = observed - predicted
    mae = float(np.mean(np.abs(residuals)))
    rmse = float(np.sqrt(np.mean(residuals ** 2)))
    total = float(np.sum((observed - np.mean(observed)) ** 2))
    r_squared = None if total <= 0 else float(1.0 - np.sum(residuals ** 2) / total)
    n = observed.size
    # Gaussian-likelihood AIC. Reported as a relative model-comparison number
    # only; it assumes independent residuals, which serially correlated
    # environmental data does not really provide.
    sse = float(np.sum(residuals ** 2))
    aic = (
        None
        if sse <= 0 or n <= parameter_count + 1
        else float(n * np.log(sse / n) + 2 * parameter_count)
    )
    return mae, rmse, r_squared, aic


def _fit_models(series, settings):
    """Fit the candidate empirical trajectories to a post-peak series."""
    hours = (series.index - series.index[0]).total_seconds().to_numpy() / 3600.0
    values = series.to_numpy(dtype="float64")
    fits = []

    # Linear: M(t) = a + b t
    design = np.column_stack([np.ones_like(hours), hours])
    coefficients, *_ = np.linalg.lstsq(design, values, rcond=None)
    predicted = design @ coefficients
    mae, rmse, r_squared, aic = _metrics(values, predicted, 2)
    fits.append(
        ModelFit(
            name="linear",
            formula="M(t) = a + b*t   (t in hours)",
            parameters={"a": float(coefficients[0]), "b": float(coefficients[1])},
            samples=int(values.size),
            mae=mae,
            rmse=rmse,
            r_squared=r_squared,
            aic=aic,
            accepted=True,
        )
    )

    # Exponential relaxation: M(t) = M_inf + A exp(-k t)
    grid = np.linspace(
        settings.exponential_rate_grid_min,
        settings.exponential_rate_grid_max,
        settings.exponential_rate_grid_points,
    )
    best = None
    for rate in grid:
        basis = np.column_stack([np.ones_like(hours), np.exp(-rate * hours)])
        try:
            solution, *_ = np.linalg.lstsq(basis, values, rcond=None)
        except np.linalg.LinAlgError:  # pragma: no cover - defensive
            continue
        residual = float(np.sum((values - basis @ solution) ** 2))
        if best is None or residual < best[0]:
            best = (residual, float(rate), solution)

    if best is None:
        fits.append(
            ModelFit(
                name="exponential_relaxation",
                formula="M(t) = M_inf + A*exp(-k*t)",
                samples=int(values.size),
                accepted=False,
                rejection_reason="no solvable rate constant on the search grid",
            )
        )
        return tuple(fits)

    _, rate, solution = best
    predicted = np.column_stack([np.ones_like(hours), np.exp(-rate * hours)]) @ solution
    mae, rmse, r_squared, aic = _metrics(values, predicted, 3)
    at_edge = rate <= grid[0] + 1e-12 or rate >= grid[-1] - 1e-12
    fits.append(
        ModelFit(
            name="exponential_relaxation",
            formula="M(t) = M_inf + A*exp(-k*t)   (t in hours, k in 1/hour)",
            parameters={
                "M_inf": float(solution[0]),
                "A": float(solution[1]),
                "k": float(rate),
                "half_life_hours": (
                    float(np.log(2) / rate) if rate > 0 else None
                ),
            },
            samples=int(values.size),
            mae=mae,
            rmse=rmse,
            r_squared=r_squared,
            aic=aic,
            accepted=not at_edge,
            rejection_reason=(
                "the best rate constant sits on the edge of the search grid, so "
                "the fit is not identified by these data"
                if at_edge
                else None
            ),
        )
    )
    return tuple(fits)
