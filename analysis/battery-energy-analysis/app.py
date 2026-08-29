"""Interactive research dashboard for the Better With Bees battery analysis."""

import contextlib
import io
import math
import os
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from battery_analysis import (
    DEFAULT_RELIABILITY_PROJECT,
    MIN_TREND_COVERAGE,
    MIN_TREND_SAMPLES,
    STATION_TIMEZONE,
    add_slot_index,
    analyze_relationships,
    build_battery_summary,
    compute_daily_battery_metrics,
    compute_gaps,
    compute_outage_battery_context,
    compute_rolling_battery_metrics,
    detect_outages,
    load_and_validate_data,
    load_reliability_exports,
    reliability_exports_match_data,
    significant_outages,
)
from energy_model import EnergyModelParameters, model_daily_power_budget


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_PATH = DEFAULT_RELIABILITY_PROJECT / "data" / "HistoricalData.csv"
DEFAULT_RELIABILITY_OUTPUT = DEFAULT_RELIABILITY_PROJECT / "audit_output"
DATA_PATH = Path(os.environ.get("BWB_HISTORICAL_CSV", DEFAULT_DATA_PATH)).expanduser()
RELIABILITY_OUTPUT = Path(
    os.environ.get("BWB_RELIABILITY_OUTPUT_DIR", DEFAULT_RELIABILITY_OUTPUT)
).expanduser()

TEAL = "#176B68"
TEAL_LIGHT = "#8BBDB5"
ORANGE = "#D18A3D"
RED = "#B54135"
INK = "#17211F"
MUTED = "#63716C"
GRID = "#D5DFDA"

RELATIONSHIP_COLUMNS = {
    "daily minimum battery voltage": "battery_min_v",
    "daily mean battery voltage": "battery_mean_v",
    "daily net battery voltage change": "battery_net_change_v",
    "rolling 72h voltage slope": "rolling_72h_slope_v_per_day",
    "latest daily battery voltage": "battery_last_v",
    "daily battery voltage change": "battery_net_change_v",
    "daytime voltage change": "daytime_voltage_change_v",
    "telemetry completeness": "telemetry_completeness",
    "significant gap count": "significant_gap_count",
    "mean temperature": "temperature_mean_c",
    "minimum temperature": "temperature_min_c",
}


st.set_page_config(
    page_title="Battery & Energy Research | Better With Bees",
    page_icon="🔋",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.html(
    """
    <style>
      [data-testid="stMainBlockContainer"] {
        max-width: 1480px;
        padding-top: 2.25rem;
        padding-bottom: 4rem;
      }
      .bwb-kicker {
        color: #176B68;
        font-size: 0.76rem;
        font-weight: 700;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        margin-bottom: 0.35rem;
      }
      .bwb-deck {
        color: #52605B;
        font-size: 1.03rem;
        max-width: 920px;
        line-height: 1.55;
        margin: 0.2rem 0 0.9rem 0;
      }
      .bwb-note {
        border-left: 3px solid #D18A3D;
        background: #F6F3EC;
        color: #48534F;
        padding: 0.75rem 0.9rem;
        border-radius: 0 6px 6px 0;
        font-size: 0.91rem;
      }
      [data-testid="stMetric"] {
        background: #FFFFFF;
        box-shadow: 0 1px 2px rgba(23, 33, 31, 0.035);
      }
      [data-testid="stMetricValue"] {
        color: #17211F;
      }
      [data-testid="stCaptionContainer"] {
        color: #63716C;
      }
      hr {
        border-color: #D5DFDA !important;
      }
      @media (max-width: 700px) {
        [data-testid="stMainBlockContainer"] { padding-top: 1.2rem; }
        .bwb-deck { font-size: 0.95rem; }
      }
    </style>
    """
)


def file_fingerprint(path):
    """Return hashable file metadata so Streamlit invalidates cached analysis."""
    path = Path(path).resolve()
    if not path.exists() or not path.is_file():
        return str(path), None, None
    stat = path.stat()
    return str(path), stat.st_mtime_ns, stat.st_size


def analysis_fingerprint(csv_path, reliability_output):
    return (
        file_fingerprint(csv_path),
        file_fingerprint(Path(reliability_output) / "outage_intervals.csv"),
        file_fingerprint(Path(reliability_output) / "daily_reliability.csv"),
    )


@st.cache_data(max_entries=8, show_spinner=False)
def load_analysis(csv_path, reliability_output, fingerprint):
    """Load and compute through the project's existing analytical functions."""
    del fingerprint  # The value exists solely as a cache key.
    validation_output = io.StringIO()
    with contextlib.redirect_stdout(validation_output):
        frame, _ = load_and_validate_data(csv_path)

    outages, reliability_daily = load_reliability_exports(reliability_output)
    reliability_source = "exported reliability-audit CSVs"
    if outages is None or not reliability_exports_match_data(frame, reliability_daily):
        indexed = add_slot_index(frame)
        outages = detect_outages(indexed, compute_gaps(indexed))
        reliability_daily = None
        reliability_source = "stable reliability helpers (exports unavailable or stale)"

    daily = compute_daily_battery_metrics(
        frame, outages=outages, reliability_daily=reliability_daily
    )
    rolling = compute_rolling_battery_metrics(frame)
    outage_context = compute_outage_battery_context(frame, outages)
    relationships = analyze_relationships(daily)
    summary = build_battery_summary(
        frame, daily, outage_context, relationships
    )
    summary["reliability_context_source"] = reliability_source
    return {
        "daily": daily,
        "rolling": rolling,
        "outages": significant_outages(outages).reset_index(drop=True),
        "outage_context": outage_context,
        "relationships": relationships,
        "summary": summary,
        "validation_log": validation_output.getvalue(),
    }


def v(value, digits=3, suffix=" V"):
    if value is None or pd.isna(value):
        return "Not available"
    return f"{float(value):.{digits}f}{suffix}"


def signed_v(value, digits=3, suffix=" V"):
    if value is None or pd.isna(value):
        return "Not available"
    return f"{float(value):+.{digits}f}{suffix}"


def format_date_range(start, end):
    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    return f"{start:%d %b %Y} – {end:%d %b %Y}"


def chart_base(chart):
    return chart.configure_axis(
        labelColor=MUTED,
        titleColor=INK,
        domainColor=GRID,
        gridColor=GRID,
        gridOpacity=0.55,
        labelFontSize=12,
        titleFontSize=12,
    ).configure_view(strokeOpacity=0)


def sampled_history(frame, maximum_rows=4500):
    plotted = frame[[
        "timestamp", "battery_voltage_v", "rolling_mean_24h_v"
    ]].dropna(subset=["timestamp", "battery_voltage_v"]).copy()
    sampled = False
    if len(plotted) > maximum_rows:
        stride = math.ceil(len(plotted) / maximum_rows)
        keep = list(range(0, len(plotted), stride))
        if keep[-1] != len(plotted) - 1:
            keep.append(len(plotted) - 1)
        plotted = plotted.iloc[keep].copy()
        sampled = True
    gap_threshold = pd.Timedelta(minutes=30)
    plotted["segment"] = plotted["timestamp"].diff().gt(gap_threshold).cumsum()
    return plotted, sampled


def history_chart(history, outages):
    base = alt.Chart(history).encode(
        x=alt.X(
            "timestamp:T",
            title=f"Time ({STATION_TIMEZONE.key})",
            axis=alt.Axis(format="%b %d"),
        )
    )
    observed = base.mark_line(color=MUTED, opacity=0.34, strokeWidth=1).encode(
        y=alt.Y("battery_voltage_v:Q", title="Battery voltage (V)", scale=alt.Scale(zero=False)),
        detail="segment:N",
    )
    rolling = base.mark_line(color=TEAL, strokeWidth=2).encode(
        y=alt.Y("rolling_mean_24h_v:Q", title="Battery voltage (V)", scale=alt.Scale(zero=False)),
        detail="segment:N",
    )
    tooltip = base.mark_circle(opacity=0, size=70).encode(
        y=alt.Y("battery_voltage_v:Q", scale=alt.Scale(zero=False)),
        tooltip=[
            alt.Tooltip("timestamp:T", title="Timestamp", format="%Y-%m-%d %H:%M"),
            alt.Tooltip("battery_voltage_v:Q", title="Observed", format=".4f"),
            alt.Tooltip("rolling_mean_24h_v:Q", title="24 h mean", format=".4f"),
        ],
    )
    layers = [observed, rolling, tooltip]
    if not outages.empty:
        outage_rules = alt.Chart(outages).mark_rule(
            color=RED, opacity=0.48, strokeWidth=1, strokeDash=[4, 3]
        ).encode(
            x="gap_start:T",
            tooltip=[
                alt.Tooltip("gap_start:T", title="Outage start", format="%Y-%m-%d %H:%M"),
                alt.Tooltip("gap_minutes:Q", title="Duration (min)", format=",.1f"),
                alt.Tooltip("severity:N", title="Severity"),
            ],
        )
        layers.append(outage_rules)
    return chart_base(alt.layer(*layers).properties(height=440))


def daily_profile_chart(daily):
    base = alt.Chart(daily).encode(
        x=alt.X("date:T", title="Date", axis=alt.Axis(format="%b %d"))
    )
    band = base.mark_area(color=TEAL_LIGHT, opacity=0.30).encode(
        y=alt.Y("battery_min_v:Q", title="Battery voltage (V)", scale=alt.Scale(zero=False)),
        y2="battery_max_v:Q",
        tooltip=[
            alt.Tooltip("date:T", title="Date"),
            alt.Tooltip("battery_min_v:Q", title="Minimum", format=".3f"),
            alt.Tooltip("battery_max_v:Q", title="Maximum", format=".3f"),
        ],
    )
    mean = base.mark_line(color=TEAL, strokeWidth=2).encode(
        y=alt.Y("battery_mean_v:Q", scale=alt.Scale(zero=False)),
        tooltip=[
            alt.Tooltip("date:T", title="Date"),
            alt.Tooltip("battery_mean_v:Q", title="Daily mean", format=".3f"),
            alt.Tooltip("battery_range_v:Q", title="Daily range", format=".3f"),
        ],
    )
    median = base.mark_line(color=ORANGE, strokeWidth=1.5, strokeDash=[5, 3]).encode(
        y=alt.Y("battery_median_v:Q", scale=alt.Scale(zero=False)),
        tooltip=[
            alt.Tooltip("date:T", title="Date"),
            alt.Tooltip("battery_median_v:Q", title="Daily median", format=".3f"),
        ],
    )
    return chart_base(alt.layer(band, mean, median).properties(height=280))


def net_change_chart(daily):
    chart = alt.Chart(daily).mark_bar().encode(
        x=alt.X("date:T", title="Date", axis=alt.Axis(format="%b %d")),
        y=alt.Y("battery_net_change_v:Q", title="First-to-last change (V)"),
        color=alt.condition(
            alt.datum.battery_net_change_v >= 0,
            alt.value(TEAL),
            alt.value(ORANGE),
        ),
        tooltip=[
            alt.Tooltip("date:T", title="Date"),
            alt.Tooltip("battery_net_change_v:Q", title="Net change", format="+.4f"),
            alt.Tooltip("battery_valid_samples:Q", title="Valid samples", format=",.0f"),
        ],
    ).properties(height=230)
    return chart_base(chart)


def net_change_distribution(daily):
    usable = daily.dropna(subset=["battery_net_change_v"])
    chart = alt.Chart(usable).mark_bar(color=TEAL, opacity=0.78).encode(
        x=alt.X(
            "battery_net_change_v:Q",
            bin=alt.Bin(maxbins=24),
            title="Daily first-to-last voltage change (V)",
        ),
        y=alt.Y("count():Q", title="Days"),
        tooltip=[alt.Tooltip("count():Q", title="Days")],
    ).properties(height=220)
    return chart_base(chart)


def strongest_relationship(relationships, category):
    usable = [
        item for item in relationships
        if item["category"] == category and item["spearman_rho"] is not None
    ]
    return max(usable, key=lambda item: abs(item["spearman_rho"])) if usable else None


def relationship_frame(daily, relationship):
    x_name = RELATIONSHIP_COLUMNS.get(relationship["x"])
    if relationship["y"] == "significant outage occurrence":
        y = (daily["significant_gap_count"] > 0).astype(int)
    else:
        y_name = RELATIONSHIP_COLUMNS.get(relationship["y"])
        y = daily[y_name] if y_name else pd.Series(index=daily.index, dtype=float)
    if not x_name:
        return pd.DataFrame()
    return pd.DataFrame({
        "date": pd.to_datetime(daily["date"]),
        "x": daily[x_name],
        "y": y,
        "significant_gaps": daily["significant_gap_count"],
    }).dropna(subset=["x", "y"])


def relationship_chart(daily, relationship, y_percent=False):
    plotted = relationship_frame(daily, relationship)
    if y_percent:
        plotted["y"] = 100 * plotted["y"]
    chart = alt.Chart(plotted).mark_circle(
        size=72, color=TEAL, opacity=0.72, stroke="white", strokeWidth=0.7
    ).encode(
        x=alt.X("x:Q", title=relationship["x"], scale=alt.Scale(zero=False)),
        y=alt.Y(
            "y:Q",
            title=(relationship["y"] + (" (%)" if y_percent else "")),
            scale=alt.Scale(zero=False),
        ),
        size=alt.Size(
            "significant_gaps:Q",
            title="Significant gaps",
            scale=alt.Scale(range=[45, 160]),
            legend=None,
        ),
        tooltip=[
            alt.Tooltip("date:T", title="Date"),
            alt.Tooltip("x:Q", title=relationship["x"], format=".4f"),
            alt.Tooltip("y:Q", title=relationship["y"], format=".3f"),
            alt.Tooltip("significant_gaps:Q", title="Significant gaps"),
        ],
    ).properties(height=310)
    return chart_base(chart)


def outage_focus_chart(rolling, outage):
    start = pd.Timestamp(outage["outage_start"])
    end = pd.Timestamp(outage["outage_end"])
    view_start = (start.tz_convert("UTC") - pd.Timedelta(hours=24)).tz_convert(STATION_TIMEZONE)
    view_end = (end.tz_convert("UTC") + pd.Timedelta(hours=6)).tz_convert(STATION_TIMEZONE)
    plotted = rolling[
        (rolling["timestamp"] >= view_start) & (rolling["timestamp"] <= view_end)
    ][["timestamp", "battery_voltage_v"]].copy()
    plotted["segment"] = plotted["timestamp"].diff().gt(pd.Timedelta(minutes=30)).cumsum()
    voltage = alt.Chart(plotted).mark_line(color=TEAL, strokeWidth=2).encode(
        x=alt.X("timestamp:T", title=f"Time ({STATION_TIMEZONE.key})"),
        y=alt.Y("battery_voltage_v:Q", title="Battery voltage (V)", scale=alt.Scale(zero=False)),
        detail="segment:N",
        tooltip=[
            alt.Tooltip("timestamp:T", title="Timestamp", format="%Y-%m-%d %H:%M"),
            alt.Tooltip("battery_voltage_v:Q", title="Voltage", format=".4f"),
        ],
    )
    interval = pd.DataFrame({"start": [start], "end": [end]})
    shade = alt.Chart(interval).mark_rect(color=RED, opacity=0.10).encode(
        x="start:T", x2="end:T"
    )
    boundary = alt.Chart(pd.DataFrame({"moment": [start, end]})).mark_rule(
        color=RED, opacity=0.65, strokeDash=[4, 3]
    ).encode(x="moment:T")
    return chart_base(alt.layer(shade, voltage, boundary).properties(height=330))


def outage_label(row):
    start = pd.Timestamp(row["outage_start"])
    duration = float(row["gap_minutes"]) / 60.0
    return (
        f"#{int(row['outage_number'])} · {start:%Y-%m-%d %H:%M} · "
        f"{duration:,.1f} h · {str(row['severity']).title()}"
    )


def subsection(title, description=None):
    st.subheader(title)
    if description:
        st.caption(description)


st.html('<div class="bwb-kicker">Better With Bees · Field telemetry research</div>')
st.title("Battery & energy research")
st.html(
    '<p class="bwb-deck">Observed voltage history, derived operating patterns, '
    'and evidence-bounded reliability context for the solar-powered sensing node.</p>'
)
with st.container(horizontal=True, gap="small"):
    st.badge("Observed telemetry", icon=":material/sensors:", color="green")
    st.badge("Derived statistics", icon=":material/analytics:", color="blue")
    st.badge("Uncalibrated model", icon=":material/science:", color="orange")

if not DATA_PATH.exists():
    st.error("The historical telemetry CSV could not be found.", icon=":material/error:")
    st.code(str(DATA_PATH), language=None)
    st.caption(
        "Set BWB_HISTORICAL_CSV to a readable HistoricalData.csv path and restart the app."
    )
    st.stop()

try:
    with st.spinner("Validating telemetry and preparing battery research views…"):
        analysis = load_analysis(
            str(DATA_PATH.resolve()),
            str(RELIABILITY_OUTPUT.resolve()),
            analysis_fingerprint(DATA_PATH, RELIABILITY_OUTPUT),
        )
except (SystemExit, ValueError, KeyError, OSError, pd.errors.ParserError) as error:
    st.error("The telemetry source could not be analyzed safely.", icon=":material/error:")
    st.code(str(error), language=None)
    st.caption("The source file was not modified. Correct the input and restart the app.")
    st.stop()

summary = analysis["summary"]
if summary.get("status") != "complete":
    st.warning(
        summary.get("reason", "No valid battery telemetry is available."),
        icon=":material/warning:",
    )
    st.caption(
        "The dashboard will not infer voltage, charge behavior, or energy state from absent data."
    )
    st.stop()

daily = analysis["daily"]
rolling = analysis["rolling"]
outages = analysis["outages"]
outage_context = analysis["outage_context"]
relationships = analysis["relationships"]
quality = summary["data_quality"]
derived = summary["derived_summary"]

latest = rolling.dropna(subset=["battery_voltage_v"]).iloc[-1]
with st.container(horizontal=True, wrap=True, gap="small"):
    st.metric(
        "Latest observed voltage",
        v(latest["battery_voltage_v"], 3),
        help="Most recent valid observed battery-voltage reading.",
        icon=":material/electric_bolt:",
        border=True,
        width=245,
    )
    st.metric(
        "Valid battery period",
        f"{pd.Timestamp(quality['first_valid_timestamp']):%d %b} – {pd.Timestamp(quality['last_valid_timestamp']):%d %b %Y}",
        help="First through last valid battery observation; not the full station history.",
        icon=":material/date_range:",
        border=True,
        width=275,
    )
    st.metric(
        "Completeness since commissioning",
        f"{100 * quality['completeness_since_commissioning']:.2f}%",
        help="Valid battery readings divided by received rows since the first valid battery reading.",
        icon=":material/check_circle:",
        border=True,
        width=275,
    )
    st.metric(
        "Observed voltage range",
        f"{quality['observed_min_voltage_v']:.3f} – {quality['observed_max_voltage_v']:.3f} V",
        help="Observed range after the project's existing broad validity rule; not an operating recommendation.",
        icon=":material/height:",
        border=True,
        width=275,
    )

st.html(
    '<div class="bwb-note"><strong>Interpretation boundary.</strong> Voltage is an observed '
    'electrical signal—not battery percentage, state of charge, remaining runtime, stored '
    'energy, or battery health.</div>'
)

st.divider()
subsection(
    "Voltage history",
    "Observed samples, a derived 24-hour rolling mean, genuine data gaps, and significant outage starts.",
)

period = st.segmented_control(
    "Display period",
    ["Full", "90 days", "30 days", "7 days", "Custom"],
    default="Full",
    width="stretch",
)
history_start = rolling["timestamp"].min()
history_end = rolling["timestamp"].max()
if period == "Custom":
    chosen = st.date_input(
        "Date interval",
        value=(history_start.date(), history_end.date()),
        min_value=history_start.date(),
        max_value=history_end.date(),
        format="YYYY-MM-DD",
    )
    if isinstance(chosen, (tuple, list)) and len(chosen) == 2:
        history_start = pd.Timestamp(chosen[0], tz=STATION_TIMEZONE)
        history_end = (
            pd.Timestamp(chosen[1], tz=STATION_TIMEZONE)
            + pd.Timedelta(days=1)
            - pd.Timedelta(microseconds=1)
        )
elif period != "Full":
    days = int(period.split()[0])
    history_start = max(history_start, history_end - pd.Timedelta(days=days))

visible_rolling = rolling[
    (rolling["timestamp"] >= history_start) & (rolling["timestamp"] <= history_end)
]
visible_outages = outages[
    (outages["gap_start"] >= history_start) & (outages["gap_start"] <= history_end)
].nlargest(30, "gap_minutes")
history_plot, sampled = sampled_history(visible_rolling)
st.altair_chart(history_chart(history_plot, visible_outages), width="stretch")
caption = format_date_range(history_start, min(history_end, rolling["timestamp"].max()))
if sampled:
    caption += " · Lightly sampled for display; calculations use the complete validated series."
st.caption(caption + " · Dashed red rules mark significant outage starts.")

st.divider()
subsection(
    "Daily voltage dynamics",
    "Daily min–max span with mean and median voltage above; observed first-to-last daily change below.",
)
visible_daily = daily[
    (pd.to_datetime(daily["date"]).dt.date >= history_start.date())
    & (pd.to_datetime(daily["date"]).dt.date <= history_end.date())
].copy()
visible_daily["date"] = pd.to_datetime(visible_daily["date"])
st.altair_chart(daily_profile_chart(visible_daily), width="stretch")
st.altair_chart(net_change_chart(visible_daily), width="stretch")
st.caption(
    f"Typical robust daily range: {v(derived['typical_daily_voltage_range_v'])} · "
    f"Median daily net change: {signed_v(derived['median_daily_net_voltage_change_v'])}. "
    "Calendar days without valid battery data remain gaps."
)

st.divider()
subsection(
    "Charge- and discharge-like behavior",
    "Voltage-direction proxies only. They do not measure charge current, energy flow, or state of charge.",
)
latest_24 = rolling["voltage_change_24h_v"].dropna()
latest_72 = rolling["rolling_slope_72h_v_per_day"].dropna()
positive = derived["strongest_daily_positive_change"]
negative = derived["strongest_daily_negative_change"]
with st.container(horizontal=True, wrap=True, gap="small"):
    st.metric("Latest 24 h change", signed_v(latest_24.iloc[-1] if not latest_24.empty else None), border=True, width=220)
    st.metric("Latest 72 h slope", signed_v(latest_72.iloc[-1] if not latest_72.empty else None, suffix=" V/day"), border=True, width=220)
    st.metric("Strongest positive day", signed_v(positive["net_voltage_change_v"] if positive else None), delta=positive["date"] if positive else None, delta_color="off", border=True, width=230)
    st.metric("Strongest negative day", signed_v(negative["net_voltage_change_v"] if negative else None), delta=negative["date"] if negative else None, delta_color="off", border=True, width=230)
st.altair_chart(net_change_distribution(visible_daily), width="stretch")
st.caption(
    f"Trends require at least {MIN_TREND_SAMPLES} readings and {MIN_TREND_COVERAGE:.0%} window coverage. "
    "Positive and negative changes are descriptive voltage behavior, not direct charging or discharging measurements."
)

st.divider()
subsection(
    "Battery ↔ reliability",
    "The strongest available day-level association is shown without a fitted trend line to avoid overstating a weak relationship.",
)
reliability_relationship = strongest_relationship(relationships, "reliability")
if reliability_relationship:
    col_chart, col_readout = st.columns([2.1, 1], gap="large")
    with col_chart:
        st.altair_chart(
            relationship_chart(
                daily,
                reliability_relationship,
                y_percent=reliability_relationship["y"] == "telemetry completeness",
            ),
            width="stretch",
        )
    with col_readout:
        st.metric("Spearman ρ", f"{reliability_relationship['spearman_rho']:+.3f}", border=True)
        st.metric("Paired days", f"{reliability_relationship['sample_size']}", border=True)
        st.caption(
            f"{reliability_relationship['x'].capitalize()} vs "
            f"{reliability_relationship['y']}."
        )
        st.info(reliability_relationship["interpretation"], icon=":material/info:")
        st.warning(
            "This weak association is not operationally useful as a standalone power or outage indicator.",
            icon=":material/warning:",
        )
else:
    st.info("No reliability relationship has enough usable variation to display.")

st.divider()
subsection(
    "Environmental context",
    "Temperature is prioritized because its field meaning is documented; humidity and raw rain/wetness remain secondary.",
)
environment_relationship = strongest_relationship(relationships, "environment")
if environment_relationship:
    col_chart, col_readout = st.columns([2.1, 1], gap="large")
    with col_chart:
        st.altair_chart(
            relationship_chart(daily, environment_relationship), width="stretch"
        )
    with col_readout:
        st.metric("Spearman ρ", f"{environment_relationship['spearman_rho']:+.3f}", border=True)
        st.metric("Paired days", f"{environment_relationship['sample_size']}", border=True)
        st.info(environment_relationship["interpretation"], icon=":material/info:")
        st.caption(
            "Temperature can affect electrochemistry, load response, charging conditions, and measurement behavior. "
            "This descriptive association cannot separate those mechanisms."
        )

st.divider()
subsection(
    "Outage investigation",
    "Inspect observed battery context before and after a significant delivery gap; this view does not assign root cause.",
)
if outage_context.empty:
    st.info("No significant outages are available for focused inspection.")
else:
    usable_indices = outage_context.index[outage_context["usable_pre_outage_context"]].tolist()
    default_index = usable_indices[0] if usable_indices else 0
    option_indices = outage_context.index.tolist()
    selected_index = st.selectbox(
        "Significant outage",
        option_indices,
        index=option_indices.index(default_index),
        format_func=lambda index: outage_label(outage_context.loc[index]),
    )
    selected = outage_context.loc[selected_index]
    st.altair_chart(outage_focus_chart(rolling, selected), width="stretch")
    with st.container(horizontal=True, wrap=True, gap="small"):
        st.metric("Outage duration", f"{selected['gap_minutes'] / 60:,.1f} h", border=True, width=210)
        st.metric("24 h pre-outage samples", f"{int(selected['pre_24h_valid_samples'])}", border=True, width=220)
        st.metric("Latest pre-outage voltage", v(selected["pre_24h_latest_voltage_v"]), border=True, width=235)
        st.metric("24 h pre-outage change", signed_v(selected["pre_24h_voltage_change_v"]), border=True, width=235)
        st.metric("First recovery voltage", v(selected["first_recovery_voltage_v"]), border=True, width=220)
    observed_box, suggestive_box, unresolved_box = st.columns(3, gap="medium")
    with observed_box:
        st.markdown("**Observed**")
        st.write(selected["observed"])
    with suggestive_box:
        st.markdown("**Suggestive**")
        st.write(selected["suggestive"])
    with unresolved_box:
        st.markdown("**Not determinable**")
        st.write(selected["not_proven"])

st.divider()
subsection(
    "Experimental energy model",
    "Uncalibrated and input-driven. Enter measured or documented hardware parameters; the dashboard supplies no hidden defaults.",
)
st.warning(
    "Modeled results are separate from observed telemetry. Do not use them as battery percentage, runtime, or field calibration.",
    icon=":material/science:",
)
with st.form("energy_model_form", border=True):
    input_a, input_b = st.columns(2, gap="large")
    with input_a:
        active_current = st.number_input("Active current (mA) *", min_value=0.0, value=None, placeholder="Required")
        sleep_current = st.number_input("Sleep current (mA) *", min_value=0.0, value=None, placeholder="Required")
        active_duration = st.number_input("Active duration per cycle (s) *", min_value=0.0, value=None, placeholder="Required")
        sleep_duration = st.number_input("Sleep duration per cycle (s) *", min_value=0.0, value=None, placeholder="Required")
        cycles_per_day = st.number_input("Cycles per day *", min_value=1.0, value=None, placeholder="Required")
        sensor_current = st.number_input("Additional sensor current while active (mA)", min_value=0.0, value=None, placeholder="Optional")
        radio_current = st.number_input("Additional radio current while active (mA)", min_value=0.0, value=None, placeholder="Optional")
    with input_b:
        battery_capacity = st.number_input("Nominal battery capacity (mAh)", min_value=0.0, value=None, placeholder="Optional; not used for runtime here")
        panel_power = st.number_input("Solar panel rated power (W)", min_value=0.0, value=None, placeholder="Optional solar set")
        sun_hours = st.number_input("Equivalent sun hours per day", min_value=0.0, value=None, placeholder="Optional solar set")
        efficiency = st.number_input("Charging/system efficiency (0–1)", min_value=0.0, max_value=1.0, value=None, placeholder="Optional solar set")
        st.caption(
            "Solar power, sun hours, and efficiency must be supplied together. Capacity is accepted by the underlying model, "
            "but runtime is intentionally not presented in this uncalibrated dashboard."
        )
    submitted = st.form_submit_button("Run uncalibrated model", type="primary", icon=":material/calculate:")

if submitted:
    required = [active_current, sleep_current, active_duration, sleep_duration, cycles_per_day]
    if any(item is None for item in required):
        st.error("Complete every field marked with an asterisk before running the model.")
        st.session_state.pop("energy_model_result", None)
    else:
        try:
            parameters = EnergyModelParameters(
                active_current_ma=active_current,
                sleep_current_ma=sleep_current,
                active_duration_seconds=active_duration,
                sleep_duration_seconds=sleep_duration,
                cycles_per_day=cycles_per_day,
                sensor_current_ma=sensor_current or 0.0,
                radio_current_ma=radio_current or 0.0,
                battery_nominal_capacity_mah=battery_capacity,
                panel_rated_power_w=panel_power,
                solar_equivalent_hours=sun_hours,
                charging_efficiency=efficiency,
            )
            st.session_state["energy_model_result"] = model_daily_power_budget(parameters)
        except ValueError as error:
            st.error(str(error))
            st.session_state.pop("energy_model_result", None)

model_result = st.session_state.get("energy_model_result")
if model_result:
    st.success("Uncalibrated model completed from the supplied parameters.")
    with st.container(horizontal=True, wrap=True, gap="small"):
        st.metric(
            "Modeled daily charge consumption",
            f"{model_result['daily_charge_consumption_mah']:.2f} mAh/day",
            border=True,
            width=290,
        )
        if model_result["solar_energy_input_wh"] is not None:
            st.metric(
                "Modeled solar energy input",
                f"{model_result['solar_energy_input_wh']:.2f} Wh/day",
                border=True,
                width=270,
            )
    st.caption(
        "These modeled quantities use different units and are not subtracted into a net balance without a calibrated voltage/conversion model."
    )

st.divider()
subsection("Research framing")
frame_a, frame_b = st.columns(2, gap="large")
with frame_a:
    st.markdown("**What the current evidence supports**")
    st.markdown(
        "- High-resolution observed voltage history after battery telemetry commissioning.\n"
        "- Daily and rolling voltage-pattern summaries with explicit coverage rules.\n"
        "- Descriptive outage and temperature context with non-causal wording."
    )
with frame_b:
    st.markdown("**Next measurements that would change the analysis**")
    st.markdown(
        "- Calibrated voltage-divider readings and battery chemistry/capacity.\n"
        "- Measured active, sleep, sensor, and radio current profiles.\n"
        "- Panel specification, irradiance/orientation, and charger efficiency."
    )
    st.caption(
        "Only after those measurements and independent field validation would calibrated energy or outage-risk models be supportable."
    )

with st.expander("Methodology, provenance, and limitations"):
    st.markdown(
        "**Observed** values are direct validated telemetry. **Derived** values are calculations from those observations. "
        "**Modeled** values appear only after explicit parameter entry."
    )
    st.markdown(
        f"- Source: `{DATA_PATH}`\n"
        f"- Reliability context: {summary['reliability_context_source']}\n"
        f"- Valid battery readings: {quality['valid_reading_count']:,}\n"
        f"- Missing since commissioning: {quality['missing_since_commissioning']:,}\n"
        f"- Rejected populated readings: {quality['rejected_reading_count']:,}\n"
        f"- Zero readings: {quality['zero_reading_count']:,}; {quality['zero_semantics']}."
    )
    st.markdown("**Core limitations**")
    for limitation in summary["limitations"]:
        st.markdown(f"- {limitation}")
    st.markdown("**Calibration inputs still required**")
    for parameter in summary["hardware_parameters_needed_for_calibration"]:
        st.markdown(f"- {parameter}")
    with st.expander("Validation trace"):
        st.code(analysis["validation_log"].strip(), language=None)

st.caption(
    "Better With Bees battery research dashboard · America/Halifax local time · "
    "descriptive engineering evidence, not a battery-management system."
)
