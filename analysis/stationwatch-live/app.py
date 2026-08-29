"""Live operational dashboard for Better With Bees StationWatch.

    streamlit run app.py

All health logic lives in ``station_health``; this file only renders it.
Colours are defined once in ``PALETTE`` below and used by both the stylesheet
(``dashboard.css``) and the chart, so no colour is written twice.
"""

from html import escape
from pathlib import Path

import altair as alt
import streamlit as st

from station_health import (
    OBSERVATION_NOTE,
    UPSTREAM_DOMAINS,
    MonitorError,
    StationMonitor,
    Status,
    format_timestamp,
)


REFRESH_SECONDS = 45
RECENT_TABLE_ROWS = 8
RECENT_GAP_READINGS = 30
STYLESHEET = Path(__file__).with_name("dashboard.css")

# The single source of truth for semantic colour, shared by dashboard.css (as
# --sw-* properties) and the chart. Each value keeps a contrast ratio above 3.5
# against both the light and the dark page background, so one palette serves
# both themes and the app never has to detect which theme is active.
PALETTE = {
    "green": "#15803d",
    "amber": "#c2740a",
    "red": "#d43b3b",
    "gray": "#78827f",
    "accent": "#128b80",
}
MONO_STACK = "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace"

# Which palette entry each state uses. dashboard.css keys off the same slugs.
STATE_SLUGS = {
    Status.HEALTHY: "healthy",
    Status.AWAITING_TELEMETRY: "awaiting",
    Status.DELAYED: "delayed",
    Status.OFFLINE: "offline",
    Status.SCHEDULED_INACTIVE: "inactive",
}


def apply_design_tokens():
    """Publish the palette as CSS variables, then load the stylesheet."""
    variables = "\n".join(f"  --sw-{name}: {value};" for name, value in PALETTE.items())
    st.html(
        f"<style>\n:root {{\n{variables}\n  --sw-mono: {MONO_STACK};\n}}\n"
        f"{STYLESHEET.read_text(encoding='utf-8')}\n</style>"
    )


def render_header():
    """Identity and purpose, stated once at the top of the page."""
    st.html(
        '<div class="sw-eyebrow">Better With Bees</div>'
        '<div class="sw-title">StationWatch Live</div>'
        '<p class="sw-subtitle">Live telemetry delivery monitoring</p>'
    )


def render_hero(state_slug, label, summary, age_label, age_value, facts):
    """The dominant element: current state, telemetry age, and key timestamps.

    ``facts`` is a sequence of ``(label, value)`` pairs shown along the footer.
    """
    fact_markup = "".join(
        f'<div><div class="sw-hero__fact-label">{escape(label)}</div>'
        f'<div class="sw-hero__fact-value">{escape(value)}</div></div>'
        for label, value in facts
    )
    st.html(
        f'<div class="sw-hero" data-state="{state_slug}">'
        '<div class="sw-hero__top">'
        "<div>"
        '<div class="sw-eyebrow">Current state</div>'
        f'<div class="sw-hero__state"><span class="sw-hero__dot"></span>{escape(label)}</div>'
        f'<p class="sw-hero__summary">{escape(summary)}</p>'
        "</div>"
        '<div class="sw-hero__age">'
        f'<div class="sw-eyebrow">{escape(age_label)}</div>'
        f'<div class="sw-hero__age-value">{escape(age_value)}</div>'
        "</div>"
        "</div>"
        f'<div class="sw-hero__facts">{fact_markup}</div>'
        "</div>"
    )


def _hero_facts(report):
    """The (label, value) pairs shown along the hero footer."""
    facts = [
        ("Latest telemetry", format_timestamp(report.latest_timestamp)),
        ("Last checked", format_timestamp(report.checked_at)),
        ("Expected window", report.window_text),
    ]
    if report.status is Status.SCHEDULED_INACTIVE:
        facts.append(("Telemetry due", report.resumes_text))
    return facts


def render_secondary_metrics(report):
    """Compact operational numbers: what is expected, and where we actually are."""
    thresholds = report.thresholds
    if report.status is Status.SCHEDULED_INACTIVE:
        # The freshness limits are not being applied right now, so showing them
        # as live numbers would imply a judgement that is not being made.
        st.caption(
            ":material/bedtime: Freshness limits are not applied outside the "
            "operating window. Telemetry is due at "
            f"{report.resumes_text}."
        )
        return

    delay_minutes = report.age_seconds / 60 - thresholds.expected_interval_minutes
    with st.container(horizontal=True):
        st.metric(
            "Expected interval",
            f"~{thresholds.expected_interval_minutes:g} min",
            border=True,
            help="How often the station is expected to sample. Context only — it is "
            "not used to classify the status.",
        )
        st.metric(
            "Beyond expected",
            f"{delay_minutes:.0f} min" if delay_minutes >= 0.5 else "On schedule",
            border=True,
            help="How far the newest reading is past the expected sampling interval.",
        )
        st.metric(
            "Healthy limit",
            f"≤ {thresholds.healthy_max_minutes:g} min",
            border=True,
            help="Telemetry at or under this age is HEALTHY.",
        )
        st.metric(
            "Offline limit",
            f"> {thresholds.offline_min_minutes:g} min",
            border=True,
            help=f"Between the two limits the status is DELAYED; at or beyond "
            f"{thresholds.offline_min_minutes:g} minutes it is OFFLINE.",
        )


def arrival_gap_chart(gaps, thresholds):
    """One chart: how long each recent reading waited behind the one before it.

    Bars are coloured by whether the gap stayed within the healthy limit, and a
    dashed rule marks the expected sampling interval, so "is telemetry arriving
    normally?" is answerable at a glance without a legend.
    """
    data = [
        {
            "Arrival": arrival.isoformat(),
            "Gap": round(minutes, 1),
            "Within limit": "Within" if minutes <= thresholds.healthy_max_minutes else "Over",
        }
        for arrival, minutes in gaps
    ]

    bars = (
        alt.Chart(alt.Data(values=data))
        .mark_bar(size=9, cornerRadiusTopLeft=2, cornerRadiusTopRight=2)
        .encode(
            x=alt.X("Arrival:T", title="Arrival (Halifax)", axis=alt.Axis(format="%H:%M")),
            y=alt.Y("Gap:Q", title="Minutes since previous"),
            color=alt.Color(
                "Within limit:N",
                scale=alt.Scale(
                    domain=["Within", "Over"], range=[PALETTE["accent"], PALETTE["amber"]]
                ),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("Arrival:T", title="Arrival", format="%Y-%m-%d %H:%M"),
                alt.Tooltip("Gap:Q", title="Minutes since previous"),
            ],
        )
    )
    expected = (
        alt.Chart(alt.Data(values=[{"Expected": thresholds.expected_interval_minutes}]))
        .mark_rule(strokeDash=[4, 4], color=PALETTE["gray"], opacity=0.9)
        .encode(y="Expected:Q")
    )
    return (bars + expected).properties(height=250)


def render_recent_activity(report):
    """Recent arrival intervals beside the readings they came from."""
    gaps = report.recent_gaps(limit=RECENT_GAP_READINGS)
    readings = report.recent_timestamps[-RECENT_TABLE_ROWS:]

    chart_column, table_column = st.columns([2, 1], gap="medium")
    with chart_column:
        st.html('<div class="sw-section">Recent arrival intervals</div>')
        with st.container(border=True):
            if gaps:
                st.altair_chart(arrival_gap_chart(gaps, report.thresholds), width="stretch")
                caption = (
                    f"Last {len(gaps)} arrivals. The dashed line marks the expected "
                    f"~{report.thresholds.expected_interval_minutes:g} min interval; amber bars "
                    f"waited longer than the {report.thresholds.healthy_max_minutes:g} min "
                    "healthy limit."
                )
                if report.status is not Status.HEALTHY:
                    # Every bar is a completed wait. The one happening now has no
                    # bar yet, so say so rather than letting the chart look calm.
                    caption += (
                        f" The current {report.age_text} wait has no bar yet: the next "
                        "reading has not arrived."
                    )
                st.caption(caption)
            else:
                st.caption(
                    "Only one reading is available, so no arrival interval can be measured yet."
                )

    with table_column:
        st.html('<div class="sw-section">Recent readings</div>')
        with st.container(border=True):
            st.dataframe(
                {"Reading": [format_timestamp(moment) for moment in reversed(readings)]},
                hide_index=True,
                height=318,
                width="stretch",
                column_config={
                    "Reading": st.column_config.TextColumn("Halifax local time", width="medium")
                },
            )


def render_observation_boundary():
    """State plainly what the dashboard does and does not know."""
    with st.expander("Observation boundary", icon=":material/help_outline:"):
        st.write(OBSERVATION_NOTE)
        st.caption(
            "Any of these could be responsible for a delivery gap, and StationWatch "
            "cannot yet tell which: " + ", ".join(UPSTREAM_DOMAINS) + "."
        )


def render_monitor_error(error, monitor):
    """A monitoring failure, kept visibly distinct from an OFFLINE station."""
    render_hero(
        "error",
        "MONITOR ERROR",
        f"{error.summary} Current telemetry status cannot be determined.",
        "Telemetry age",
        "—",
        [("Reason", error.detail), ("Last attempt", format_timestamp(monitor.now()))],
    )
    st.caption(
        ":material/info: This is a monitoring failure, not a station outage. StationWatch has "
        "no usable observation of the data source, so no telemetry status applies — an OFFLINE "
        "reading would mean the source *was* read and its newest telemetry is stale."
    )


def render_check():
    """Run one live check and render everything that depends on it."""
    monitor = StationMonitor()
    with st.skeleton(height=210):
        try:
            report = monitor.check()
        except MonitorError as error:
            render_monitor_error(error, monitor)
            return

        render_hero(
            STATE_SLUGS[report.status],
            str(report.status),
            report.summary,
            "Telemetry age",
            report.age_text,
            _hero_facts(report),
        )

    if report.status is Status.AWAITING_TELEMETRY:
        st.caption(
            ":material/hourglass_top: Within the "
            f"{report.thresholds.startup_grace_minutes:g}-minute startup grace after the "
            "operating window reopened. The ordinary freshness limits resume once it "
            "expires, so a station that does not return is still reported."
        )

    if report.latest_is_ambiguous:
        st.caption(
            ":material/schedule: The newest timestamp falls in a daylight-saving "
            "transition hour. The Sheet stores local time with no UTC offset, so its "
            "exact instant cannot be recovered from the source alone."
        )

    render_secondary_metrics(report)
    st.space("small")
    render_recent_activity(report)


st.set_page_config(
    page_title="StationWatch Live",
    page_icon=":material/sensors:",
    layout="wide",
)
apply_design_tokens()
render_header()

controls = st.container(horizontal=True, horizontal_alignment="right")
with controls:
    # The button needs no handler: any interaction reruns the script, and every
    # run re-reads the Sheet, so a refresh never shows cached data.
    st.button("Refresh now", icon=":material/refresh:", type="primary")
    auto_refresh = st.toggle(
        f"Auto every {REFRESH_SECONDS}s",
        value=True,
        help="Re-reads the Sheet on a timer. The station samples every few minutes, "
        "so checking faster would add load without adding information.",
    )

# Only the status panel repeats on a timer; run_every=None leaves it manual.
st.fragment(render_check, run_every=REFRESH_SECONDS if auto_refresh else None)()

render_observation_boundary()
