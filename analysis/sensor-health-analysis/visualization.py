"""A small set of reproducible sensor-health diagnostic figures."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from sensor_rules import ALL_SENSORS, PRIMARY_SENSORS, SENSOR_RULES


COLOURS = {
    "Temperature": "#d73027",
    "Humidity": "#4575b4",
    "Soil Moisture": "#7b3294",
    "Air Pressure": "#1a9850",
    "Rain Value": "#8c510a",
    "Battery Voltage": "#fdae61",
}


def _finish(fig, path):
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def plot_sensor_completeness(results, output_dir):
    daily = results["daily"].copy()
    calendar = pd.date_range(daily["date"].min(), daily["date"].max(), freq="D")
    daily.index = pd.to_datetime(daily["date"])
    fig, axes = plt.subplots(2, 1, figsize=(15, 8), sharex=True)
    for sensor in ("Temperature", "Humidity", "Air Pressure"):
        series = (100 * daily[f"{sensor} interpreted completeness"]).reindex(calendar)
        axes[0].plot(calendar, series, label=sensor, color=COLOURS[sensor], linewidth=1.2)
    for sensor in ("Soil Moisture", "Rain Value", "Battery Voltage"):
        series = (100 * daily[f"{sensor} interpreted completeness"]).reindex(calendar)
        axes[1].plot(calendar, series, label=sensor, color=COLOURS[sensor], linewidth=1.2)
    for ax in axes:
        ax.set_ylim(-4, 104)
        ax.set_ylabel("populated (%)")
        ax.grid(alpha=.2)
        ax.legend(loc="lower left", ncol=3, fontsize=8)
    axes[0].set_title("Daily sensor completeness within received rows\nblank calendar days mean no telemetry arrived and are not sensor failures")
    axes[1].set_title("Soil uses documented sampling opportunities; battery starts at commissioning; rain is raw wetness ADC")
    axes[1].set_xlabel("local date (America/Halifax)")
    path = Path(output_dir) / "plot_sensor_completeness.png"
    return _finish(fig, path)


def plot_anomaly_timeline(results, output_dir):
    events = results["events"]
    significant = events[events["severity"].isin(["significant", "critical"])].copy()
    fig, ax = plt.subplots(figsize=(15, 7))
    sensors = list(ALL_SENSORS) + [
        value for value in significant["sensor"].unique() if value not in ALL_SENSORS
    ]
    y_lookup = {sensor: index for index, sensor in enumerate(sensors)}
    markers = {"significant": "o", "critical": "X"}
    colours = {"significant": "#f9a825", "critical": "#c62828"}
    for severity in ("significant", "critical"):
        rows = significant[significant["severity"] == severity]
        if rows.empty:
            continue
        ax.scatter(
            rows["start_time"], rows["sensor"].map(y_lookup),
            s=np.clip(18 + np.log1p(rows["sample_count"]) * 12, 25, 130),
            marker=markers[severity], color=colours[severity], alpha=.75,
            label=severity,
        )
        for _, row in rows[rows["duration_minutes"] > 0].iterrows():
            ax.hlines(y_lookup[row["sensor"]], row["start_time"], row["end_time"],
                      color=colours[severity], alpha=.35, linewidth=2)
    ax.set_yticks(range(len(sensors)), sensors)
    ax.set_title("Significant and critical anomaly events\npoints are evidence signatures, not component failure diagnoses")
    ax.set_xlabel("timestamp (America/Halifax)")
    ax.grid(axis="x", alpha=.2)
    ax.legend(frameon=False)
    path = Path(output_dir) / "plot_anomaly_timeline.png"
    return _finish(fig, path)


def plot_pressure_diagnostics(results, output_dir):
    data = results["data"]
    pressure = data["Air Pressure"]
    rule = SENSOR_RULES["Air Pressure"]
    valid = pressure.notna() & pressure.between(rule.impossible_low, rule.impossible_high)
    impossible = pressure.notna() & ~pressure.between(rule.impossible_low, rule.impossible_high)
    missing = pressure.isna()
    fig, (history, detail) = plt.subplots(2, 1, figsize=(15, 8), sharex=True,
                                          gridspec_kw={"height_ratios": [2, 1]})
    history.plot(data.loc[valid, "timestamp"], pressure[valid], ".", color="#2e7d32",
                 markersize=1.8, alpha=.55, label="plausible-band reading")
    history.plot(data.loc[impossible, "timestamp"], pressure[impossible], "x",
                 color="#c62828", markersize=4, label="impossible reading")
    history.scatter(data.loc[missing, "timestamp"], np.full(int(missing.sum()), 785),
                    marker="|", s=18, color="#455a64", alpha=.35,
                    label="pressure missing on received row")
    history.axhspan(rule.impossible_low, rule.impossible_high, color="#66bb6a", alpha=.06)
    history.set_ylabel("pressure (hPa)")
    history.set_title("Air-pressure history: populated, impossible, and sensor-specific missing evidence")
    history.legend(ncol=3, fontsize=8, frameon=False)
    history.grid(alpha=.15)

    detail.plot(data["timestamp"], pressure, ".", color="#546e7a", markersize=1.5, alpha=.45)
    detail.axhline(4.04, color="#c62828", linestyle="--", linewidth=1, label="known 4.04 hPa value")
    detail.set_ylim(-25, 325)
    detail.set_ylabel("pressure (hPa)")
    detail.set_xlabel("timestamp (America/Halifax)")
    detail.legend(frameon=False, fontsize=8)
    detail.grid(alpha=.15)
    path = Path(output_dir) / "plot_pressure_diagnostics.png"
    return _finish(fig, path)


def plot_rate_diagnostics(results, output_dir):
    rates = results["rates"].copy()
    fig, axes = plt.subplots(2, 3, figsize=(15, 8))
    for ax, sensor in zip(axes.flat, ALL_SENSORS):
        values = rates.loc[rates["sensor"] == sensor, "rate_per_minute"].abs()
        values = values[values > 0]
        if values.empty:
            ax.text(.5, .5, "no usable changes", ha="center", va="center")
        else:
            upper = values.quantile(.9995)
            clipped = values[values <= upper]
            ax.hist(clipped, bins=60, color=COLOURS[sensor], alpha=.8)
            threshold = results["rate_thresholds"].get(sensor)
            if threshold and np.isfinite(threshold) and threshold <= upper:
                ax.axvline(threshold, color="#212121", linestyle="--", linewidth=1,
                           label="empirical q99.9")
                ax.legend(fontsize=7, frameon=False)
        ax.set_title(sensor)
        ax.set_xlabel(f"absolute change / real minute\n({SENSOR_RULES[sensor].unit}/min)")
        ax.set_ylabel("adjacent intervals")
        ax.grid(alpha=.15)
    fig.suptitle("Empirical rate-of-change distributions\nactual UTC elapsed time; sub-minute repeat telemetry excluded", y=1.01)
    path = Path(output_dir) / "plot_rate_diagnostics.png"
    return _finish(fig, path)


def create_plots(results, output_dir):
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    return [
        plot_sensor_completeness(results, output_dir),
        plot_anomaly_timeline(results, output_dir),
        plot_pressure_diagnostics(results, output_dir),
        plot_rate_diagnostics(results, output_dir),
    ]
