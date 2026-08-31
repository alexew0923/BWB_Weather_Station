"""Diagnostic plots for validating the analysis.

These exist to check the engine's work, not to present it. They are research
artefacts: no house style, no branding, no interactivity. A future frontend
draws its own charts from the structured output.

Matplotlib is imported lazily and the Agg backend is selected, so importing the
engine never opens a window and never requires a display. Plotting is optional:
nothing in the analysis path calls this module.
"""

from pathlib import Path

import numpy as np
import pandas as pd

from . import sensors
from .baselines import wetness_deviation


def _pyplot():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as pyplot

    return pyplot


def break_at_gaps(series, gap_minutes=30.0):
    """Insert NaN at telemetry gaps so a line plot does not bridge them.

    Matplotlib joins consecutive points with a straight line, which draws a
    confident diagonal across a seven-hour overnight shutdown. That is exactly
    the "missing telemetry read as environmental behaviour" mistake the rest of
    the engine refuses to make, so the plots refuse it too.
    """
    if series.empty:
        return series
    gaps = series.index.to_series().diff().dt.total_seconds() / 60.0
    breaks = series.index[gaps > gap_minutes]
    if len(breaks) == 0:
        return series
    # A NaN placed just before each resumption severs the line there.
    marker = breaks - pd.Timedelta(microseconds=1)
    filler = pd.Series(float("nan"), index=marker)
    return pd.concat([series, filler]).sort_index()


def _save(figure, output_dir, name):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / name
    figure.savefig(path, dpi=120, bbox_inches="tight")
    _pyplot().close(figure)
    return path


def plot_wetness_with_events(dataset, events, output_dir, name="plot_wetness_events.png"):
    """The wetness signal and its dry reference, with detected events shaded."""
    pyplot = _pyplot()
    frame = wetness_deviation(dataset)
    signal = break_at_gaps(frame["signal"].dropna())
    reference = break_at_gaps(frame["reference"].dropna())
    figure, axis = pyplot.subplots(figsize=(14, 5))
    axis.plot(signal.index, signal, linewidth=0.5, label="wetness signal")
    axis.plot(
        reference.index, reference, linewidth=1.0, linestyle="--",
        label="dry reference",
    )
    for event in events:
        axis.axvspan(event.start_time, event.end_time, alpha=0.2, color="tab:blue")
    axis.set_ylabel("raw ADC counts")
    axis.set_title(
        "Wetness signal with detected wetting events "
        "(uncalibrated ADC counts, not rainfall)"
    )
    axis.legend(loc="lower left")
    return _save(figure, output_dir, name)


def plot_event_context(dataset, event, output_dir, name=None):
    """One event with humidity and temperature aligned to it."""
    pyplot = _pyplot()
    pre = event.start_time - pd.Timedelta(hours=24)
    post = event.end_time + pd.Timedelta(hours=48)
    window = dataset.subset(pre, post)

    panels = (
        (sensors.WETNESS_SIGNAL, "wetness\n(ADC counts)"),
        (sensors.HUMIDITY, "humidity\n(% RH)"),
        (sensors.TEMPERATURE, "temperature\n(deg C)"),
    )
    figure, axes = pyplot.subplots(3, 1, figsize=(12, 8), sharex=True)
    for axis, (sensor, label) in zip(axes, panels):
        values = break_at_gaps(window.series(sensor).dropna())
        axis.plot(values.index, values, linewidth=0.8)
        axis.set_ylabel(label)
    for axis in axes:
        axis.axvspan(event.start_time, event.end_time, alpha=0.15, color="tab:blue")
    axes[0].set_title(f"{event.event_id} - {event.classification}")
    return _save(figure, output_dir, name or f"plot_event_{_slug(event.event_id)}.png")


def plot_event_soil_response(dataset, event, output_dir, context=None, name=None):
    """The soil signal around one event, with its baseline and threshold."""
    pyplot = _pyplot()
    pre = event.start_time - pd.Timedelta(hours=24)
    post = event.end_time + pd.Timedelta(hours=72)
    series = (
        context.residual if context is not None else dataset.series(sensors.SOIL_SIGNAL)
    ).loc[pre:post].dropna()

    plotted = break_at_gaps(series)
    figure, axis = pyplot.subplots(figsize=(12, 4))
    axis.plot(plotted.index, plotted, marker=".", linewidth=0.7, label="soil signal")
    response = event.soil_response
    if response.baseline_counts is not None:
        axis.axhline(response.baseline_counts, linestyle="--", linewidth=1.0,
                     label="pre-event baseline")
        if response.detection_threshold_counts:
            for sign in (1, -1):
                axis.axhline(
                    response.baseline_counts
                    + sign * response.detection_threshold_counts,
                    linestyle=":", linewidth=0.8, color="tab:red",
                    label="detection threshold" if sign == 1 else None,
                )
    axis.axvspan(event.start_time, event.end_time, alpha=0.15, color="tab:blue")
    axis.set_ylabel("soil signal (ADC counts)")
    axis.set_title(
        f"{event.event_id} - soil response: {response.status} "
        "(raw counts; no calibration)"
    )
    axis.legend(loc="best", fontsize="small")
    return _save(figure, output_dir, name or f"plot_soil_{_slug(event.event_id)}.png")


def plot_event_duration_distribution(events, output_dir,
                                     name="plot_event_durations.png"):
    """Histogram of detected event durations."""
    pyplot = _pyplot()
    durations = np.array([event.duration_minutes for event in events])
    figure, axis = pyplot.subplots(figsize=(8, 4))
    if durations.size:
        axis.hist(durations, bins=30)
    axis.set_xlabel("event duration (minutes)")
    axis.set_ylabel("events")
    axis.set_title(f"Detected wetting event durations (n={durations.size})")
    return _save(figure, output_dir, name)


def plot_soil_response_vs_event(events, output_dir, name="plot_soil_vs_event.png"):
    """Soil response magnitude against wetness magnitude, where both are known."""
    pyplot = _pyplot()
    points = [
        (
            event.observations.peak_deviation_counts,
            event.soil_response.response_counts,
        )
        for event in events
        if event.soil_response.response_counts is not None
        and event.observations.peak_deviation_counts is not None
    ]
    figure, axis = pyplot.subplots(figsize=(7, 5))
    if points:
        x, y = zip(*points)
        axis.scatter(x, y, s=18)
    axis.axhline(0, linewidth=0.8, color="grey")
    axis.set_xlabel("wetness peak deviation (ADC counts)")
    axis.set_ylabel("soil signal change from baseline (ADC counts)")
    axis.set_title(
        "Soil-signal change vs wetness magnitude "
        f"(n={len(points)}; raw counts, no calibration)"
    )
    return _save(figure, output_dir, name)


def plot_post_event_trajectory(dataset, event, output_dir, context=None, name=None):
    """The post-event soil trajectory with any accepted empirical model."""
    pyplot = _pyplot()
    dynamics = event.post_event_dynamics
    series = (
        context.residual if context is not None else dataset.series(sensors.SOIL_SIGNAL)
    ).loc[event.start_time:event.end_time + pd.Timedelta(hours=72)].dropna()

    plotted = break_at_gaps(series)
    figure, axis = pyplot.subplots(figsize=(10, 4))
    axis.plot(plotted.index, plotted, marker=".", linewidth=0.7, label="observed")
    if dynamics is not None and dynamics.models and not series.empty:
        hours = (series.index - series.index[0]).total_seconds().to_numpy() / 3600.0
        for model in dynamics.models:
            if not model.accepted or not model.parameters:
                continue
            if model.name == "linear":
                fitted = model.parameters["a"] + model.parameters["b"] * hours
            else:
                fitted = model.parameters["M_inf"] + model.parameters["A"] * np.exp(
                    -model.parameters["k"] * hours
                )
            axis.plot(series.index, fitted, linewidth=1.2,
                      label=f"{model.name} (empirical)")
    axis.set_ylabel("soil signal (ADC counts)")
    axis.set_title(f"{event.event_id} - post-event trajectory")
    axis.legend(loc="best", fontsize="small")
    return _save(figure, output_dir, name or f"plot_trajectory_{_slug(event.event_id)}.png")


def _slug(value):
    return "".join(character if character.isalnum() else "-" for character in value)
