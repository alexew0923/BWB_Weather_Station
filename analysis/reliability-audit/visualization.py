"""PNG figure generation for the reliability audit."""

import os

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")  # no display on the machines this runs on
import matplotlib.dates as mdates  # noqa: E402  (backend must be set first)
import matplotlib.pyplot as plt  # noqa: E402

from audit_config import (
    BASELINE_CHANGEOVER_DATE,
    DAY_GOOD_COMPLETENESS,
    EXPECTED_TRANSMISSIONS_24H,
    EXPECTED_TRANSMISSIONS_POWERED,
    GAP_MAJOR_MINUTES,
    GAP_MINOR_MINUTES,
    GAP_MODERATE_MINUTES,
    GAP_NOMINAL_MINUTES,
    NOMINAL_CYCLE_MINUTES,
)

# --------------------------------------------------------------------------
# 6. Plots
# --------------------------------------------------------------------------

CLASS_COLOURS = {
    "Good day": "#2e7d32",
    "Minor transmission loss": "#9ccc65",
    "Sensor-level issue": "#1e88e5",
    "Partial transmission loss": "#fbc02d",
    "Severe transmission loss": "#ef6c00",
    "Over-baseline (fast-cycling / repeat transmissions)": "#8e24aa",
    "Full outage": "#c62828",
}


def plot_daily_completeness(daily_reliability, output_dir):
    """Daily row completeness over time, coloured by failure class."""
    fig, ax = plt.subplots(figsize=(15, 6))
    dates = pd.to_datetime(daily_reliability["date"])

    for label, colour in CLASS_COLOURS.items():
        # "Full outage" always has zero rows, so its bar has zero height and can
        # never be seen. It is drawn as a rug below instead; skipping it here
        # keeps it out of the legend twice.
        if label == "Full outage":
            continue
        mask = daily_reliability["failure_class"] == label
        if not mask.any():
            continue
        ax.bar(dates[mask], 100 * daily_reliability.loc[mask, "row_completeness_raw"],
               color=colour, width=1.0, label=label)

    # A zero-row day is a zero-height bar, i.e. invisible -- and zero-row days
    # are the single largest category in this dataset. Draw them as a rug below
    # the axis so the most severe class is not the least visible one.
    no_data = daily_reliability["rows_received"] == 0
    if no_data.any():
        ax.scatter(
            dates[no_data], np.full(int(no_data.sum()), -7),
            marker="|", s=70, linewidths=1.4, color=CLASS_COLOURS["Full outage"],
            label=f"Full outage, zero rows ({int(no_data.sum())} days)",
            clip_on=False,
        )

    ax.axhline(100, color="black", linewidth=1, linestyle="--",
               label=f"baseline ({EXPECTED_TRANSMISSIONS_24H}/day, then "
                     f"{EXPECTED_TRANSMISSIONS_POWERED}/day)")
    # The denominator changes here, so the y axis means something different
    # either side of this line. Mark it rather than leaving it implicit.
    # date2num gives matplotlib the float it actually wants on a date axis.
    ax.axvline(mdates.date2num(BASELINE_CHANGEOVER_DATE), color="#37474f",
               linewidth=1.2, linestyle="-.",
               label=f"baseline changeover {BASELINE_CHANGEOVER_DATE}")
    ax.axhline(100 * DAY_GOOD_COMPLETENESS, color="#2e7d32", linewidth=0.8,
               linestyle=":", label=f"good day >= {100 * DAY_GOOD_COMPLETENESS:.0f}%")
    ax.set_ylim(bottom=-12)

    ax.set_title(
        "Daily row completeness (system level)\n"
        f"share of the physically schedulable transmissions per day that arrived "
        f"({EXPECTED_TRANSMISSIONS_24H}/day until {BASELINE_CHANGEOVER_DATE}, "
        f"{EXPECTED_TRANSMISSIONS_POWERED}/day after)",
        fontsize=12,
    )
    ax.set_ylabel("row completeness (%)")
    ax.set_xlabel("date")
    ax.legend(fontsize=8, ncol=2, loc="upper left")
    ax.margins(x=0.01)
    fig.tight_layout()

    path = os.path.join(output_dir, "plot_daily_completeness.png")
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def plot_daily_largest_gap(daily_reliability, output_dir):
    """Largest real outage per day, on a log scale with the severity bands."""
    fig, ax = plt.subplots(figsize=(15, 6))
    dates = pd.to_datetime(daily_reliability["date"])
    gaps = daily_reliability["largest_gap_minutes"].replace(0, np.nan)

    ax.scatter(dates, gaps, s=14, color="#c62828", alpha=0.75)
    ax.set_yscale("log")

    for value, label, colour in [
        (GAP_NOMINAL_MINUTES, "nominal ceiling (7.5 min)", "#9e9e9e"),
        (GAP_MINOR_MINUTES, "minor / moderate (30 min)", "#fbc02d"),
        (GAP_MODERATE_MINUTES, "moderate / major (2 h)", "#ef6c00"),
        (GAP_MAJOR_MINUTES, "major / critical (8 h)", "#c62828"),
    ]:
        ax.axhline(value, linestyle="--", linewidth=0.9, color=colour, label=label)

    ax.set_title(
        "Largest real outage per day (scheduled overnight shutdowns excluded)",
        fontsize=12,
    )
    ax.set_ylabel("gap length (minutes, log scale)")
    ax.set_xlabel("date")
    ax.legend(fontsize=8, loc="upper left")
    ax.margins(x=0.01)
    fig.tight_layout()

    path = os.path.join(output_dir, "plot_daily_largest_gap.png")
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def plot_sensor_completeness(sensor_daily, output_dir):
    """
    Per-sensor field population over time, with the soil moisture anomaly and
    the battery commissioning date called out visually.

    Series are reindexed onto the full calendar so that days with NO rows at all
    become breaks in the line. Without this, matplotlib draws a straight segment
    across a 19-day outage and it reads as continuous healthy data.
    """
    fig, ax = plt.subplots(figsize=(15, 6.5))
    calendar = pd.date_range(
        sensor_daily["date"].min(), sensor_daily["date"].max(), freq="D"
    )
    data_days = pd.to_datetime(sensor_daily["date"])

    styles = {
        "Temperature": ("#e53935", 1.2),
        "Humidity": ("#1e88e5", 1.2),
        "Air Pressure": ("#43a047", 1.2),
        "Rain Value": ("#6d4c41", 1.2),
        "Soil Moisture": ("#8e24aa", 2.2),
        "Battery Voltage": ("#fb8c00", 2.2),
    }

    smoothed = {}
    for column, (colour, width) in styles.items():
        # 7-day rolling mean over the days that actually have rows: daily rates
        # are very noisy on low-row days and the point of this plot is the
        # multi-month shape, not day-to-day jitter. Reindexing afterwards keeps
        # the smoothing while still showing the holes.
        series = pd.Series(
            (100 * sensor_daily[f"{column} completeness"]).to_numpy(),
            index=data_days,
        )
        rolled = pd.Series(series.rolling(7, min_periods=1).mean())
        series = rolled.reindex(calendar)
        smoothed[column] = series
        ax.plot(calendar, series, color=colour, linewidth=width, label=column)

    _shade_no_data_days(ax, calendar, data_days)
    _annotate_soil_moisture_anomaly(ax, smoothed["Soil Moisture"])

    ax.set_title(
        "Per-sensor completeness within received rows (7-day rolling mean)\n"
        "denominator is rows actually received that day, NOT the daily "
        "transmission baseline; grey bands = no rows received at all",
        fontsize=12,
    )
    ax.set_ylabel("field populated (%)")
    ax.set_xlabel("date")
    ax.set_ylim(-6, 122)
    ax.set_yticks([0, 20, 40, 60, 80, 100])
    # Legend below the axes: every horizontal band of this plot carries data
    # (0% for a dead sensor, 100% for a healthy one), so an inset legend would
    # cover exactly the thing being read.
    ax.legend(fontsize=8, ncol=6, loc="upper center",
              bbox_to_anchor=(0.5, -0.12), frameon=False)
    ax.margins(x=0.01)
    fig.tight_layout()

    path = os.path.join(output_dir, "plot_sensor_completeness.png")
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def _shade_no_data_days(ax, calendar, data_days):
    """Grey out calendar days on which no row was received at all."""
    missing = ~calendar.isin(data_days)
    ax.fill_between(
        calendar, -6, 122, where=missing,
        color="#9e9e9e", alpha=0.16, linewidth=0, zorder=0,
    )


def _annotate_soil_moisture_anomaly(ax, soil_series):
    """
    Mark the stretch where Soil Moisture was mostly absent.

    Drawn as a bar in the headroom above the data rather than as a full-height
    axvspan, because the no-data bands are already shaded and two overlapping
    spans compound into a colour that reads as a third category.

    Bounded by the first and last day whose 7-day rolling completeness is below
    50%, so the marker follows the data rather than hard-coded dates.
    """
    failing = soil_series < 50
    if not failing.any():
        return

    failing_days = soil_series.index[failing]
    start, end = failing_days.min(), failing_days.max()

    ax.plot([start, end], [113, 113], color="#8e24aa", linewidth=5,
            solid_capstyle="butt")
    ax.text(
        start, 116,
        "Soil Moisture: unresolved multi-month failure and recovery (see README)",
        fontsize=8, color="#6a1b9a", va="bottom",
    )


def plot_gap_distribution(gaps, output_dir):
    """
    Histogram of inter-arrival times with the chosen thresholds drawn on, so the
    thresholds can be checked against the distribution they came from.
    """
    fig, (ax_short, ax_tail) = plt.subplots(1, 2, figsize=(15, 5.5))
    clean = gaps.dropna()

    ax_short.hist(clean[clean <= 45], bins=90, color="#455a64")
    ax_short.set_yscale("log")
    ax_short.axvline(GAP_NOMINAL_MINUTES, color="#9e9e9e", linestyle="--",
                     label="nominal ceiling 7.5 min")
    ax_short.axvline(GAP_MINOR_MINUTES, color="#fbc02d", linestyle="--",
                     label="minor ceiling 30 min")
    for cycle in range(1, 10):
        ax_short.axvline(cycle * NOMINAL_CYCLE_MINUTES, color="#90caf9",
                         linewidth=0.6, alpha=0.7, zorder=0)
    ax_short.set_title("Short gaps (<= 45 min)\nblue lines mark 5 min cycle multiples",
                       fontsize=11)
    ax_short.set_xlabel("gap (minutes)")
    ax_short.set_ylabel("count (log)")
    ax_short.legend(fontsize=8)

    tail = clean[clean > GAP_NOMINAL_MINUTES]
    ax_tail.hist(np.log10(tail), bins=60, color="#455a64")
    for value, colour, label in [
        (GAP_MINOR_MINUTES, "#fbc02d", "minor / moderate (30 min)"),
        (GAP_MODERATE_MINUTES, "#ef6c00", "moderate / major (2 h)"),
        (GAP_MAJOR_MINUTES, "#c62828", "major / critical (8 h)"),
    ]:
        ax_tail.axvline(np.log10(value), color=colour, linestyle="--", label=label)

    # The scheduled overnight shutdown is a real mode in this distribution and it
    # is what puts the cliff just below the 8 h threshold. Label it so the
    # threshold choice is visible rather than asserted in a comment.
    ax_tail.annotate(
        "scheduled overnight\nshutdown (~7.1 h)",
        xy=(np.log10(424), 60), xytext=(np.log10(424) - 0.95, 240),
        fontsize=8, color="#37474f", ha="center",
        arrowprops={"arrowstyle": "->", "color": "#37474f", "linewidth": 0.8},
    )

    # Label the log axis in durations people can read, not raw log10 values.
    ticks = [10, 30, 60, 120, 480, 1440, 10080, 27328]
    labels = ["10 m", "30 m", "1 h", "2 h", "8 h", "1 d", "1 wk", "19 d"]
    ax_tail.set_xticks([np.log10(t) for t in ticks])
    ax_tail.set_xticklabels(labels, fontsize=8)
    ax_tail.set_title("Tail of the gap distribution (> 7.5 min)\nseverity thresholds marked",
                      fontsize=11)
    ax_tail.set_xlabel("gap length (log scale)")
    ax_tail.set_ylabel("count")
    ax_tail.legend(fontsize=8)

    fig.tight_layout()
    path = os.path.join(output_dir, "plot_gap_distribution.png")
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path

