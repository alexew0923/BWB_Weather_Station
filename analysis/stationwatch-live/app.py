"""Live operational dashboard for Better With Bees StationWatch.

    streamlit run app.py

All health logic lives in ``station_health``; this file only renders it.
"""

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
RECENT_TABLE_ROWS = 15
RECENT_GAP_READINGS = 30

STATUS_COLOURS = {
    Status.HEALTHY: "#1f9d55",
    Status.DELAYED: "#c98a04",
    Status.OFFLINE: "#c33131",
}
ERROR_COLOUR = "#6b7280"

# Just enough CSS for a readable status indicator; the rest is native Streamlit.
STATUS_CSS = """
<style>
.sw-status { display: flex; align-items: center; gap: 0.6rem; margin: 0.2rem 0 0.4rem; }
.sw-dot { width: 0.85rem; height: 0.85rem; border-radius: 50%; flex: none; }
.sw-label { font-size: 2.4rem; font-weight: 700; letter-spacing: 0.04em; line-height: 1.1; }
.sw-summary { font-size: 1.05rem; opacity: 0.85; margin-bottom: 0.5rem; }
/* Timestamps are long; the default metric size truncates them. */
[data-testid="stMetricValue"] { font-size: 1.3rem; font-variant-numeric: tabular-nums; }
</style>
"""


def render_status_banner(label, summary, colour):
    """Render the page's most prominent element: the current status."""
    st.markdown(
        f'<div class="sw-status">'
        f'<span class="sw-dot" style="background:{colour}"></span>'
        f'<span class="sw-label" style="color:{colour}">{label}</span>'
        f"</div>"
        f'<div class="sw-summary">{summary}</div>',
        unsafe_allow_html=True,
    )


def render_measurements(report):
    """Show the latest timestamp, telemetry age, and when we last checked."""
    latest, age, checked = st.columns(3)
    latest.metric("Latest telemetry", format_timestamp(report.latest_timestamp))
    age.metric("Telemetry age", report.age_text)
    checked.metric("Last checked", format_timestamp(report.checked_at))


def render_thresholds(thresholds):
    """Show the configured thresholds as low-prominence context."""
    st.caption(
        f"Expected sampling interval: ~{thresholds.expected_interval_minutes:g} min "
        f"· Healthy: ≤ {thresholds.healthy_max_minutes:g} min "
        f"· Delayed: {thresholds.healthy_max_minutes:g}–{thresholds.offline_min_minutes:g} min "
        f"· Offline: > {thresholds.offline_min_minutes:g} min"
    )


def render_recent_context(report):
    """Show recent arrivals and how far apart they were."""
    readings = report.recent_timestamps[-RECENT_TABLE_ROWS:]
    gaps = report.recent_gaps(limit=RECENT_GAP_READINGS)

    table, chart = st.columns([1, 2])
    with table:
        st.caption(f"Most recent {len(readings)} readings")
        st.dataframe(
            {"Reading (Halifax)": [format_timestamp(moment) for moment in reversed(readings)]},
            hide_index=True,
            width="stretch",
        )
    with chart:
        st.caption(f"Minutes since previous reading (last {len(gaps)} arrivals)")
        if gaps:
            st.bar_chart(
                {
                    "Arrival": [moment for moment, _ in gaps],
                    "Minutes since previous": [round(minutes, 1) for _, minutes in gaps],
                },
                x="Arrival",
                y="Minutes since previous",
                height=260,
            )
        else:
            st.info("Only one reading is available, so no arrival gap can be shown.")


def render_observation_boundary():
    """State plainly what an OFFLINE reading does and does not prove."""
    with st.expander("What StationWatch can and cannot tell you"):
        st.write(OBSERVATION_NOTE)
        st.write("Potential upstream failure domains include:")
        st.write("\n".join(f"- {domain}" for domain in UPSTREAM_DOMAINS))
        st.caption("StationWatch does not yet diagnose which of these failed.")


def render_check():
    """Run one live check and render everything that depends on it."""
    monitor = StationMonitor()
    try:
        report = monitor.check()
    except MonitorError as error:
        render_status_banner(
            "MONITOR ERROR",
            f"{error.summary} Current telemetry status cannot be determined.",
            ERROR_COLOUR,
        )
        st.warning(f"Detail: {error.detail}")
        st.caption(
            "This is a monitoring failure, not a station outage: StationWatch has "
            "no usable observation of the data source, so no telemetry status applies."
        )
        render_thresholds(monitor.thresholds)
        return

    render_status_banner(str(report.status), report.summary, STATUS_COLOURS[report.status])
    render_measurements(report)
    render_thresholds(report.thresholds)
    st.divider()
    render_recent_context(report)


def main():
    st.set_page_config(page_title="StationWatch Live", page_icon="●", layout="centered")
    st.markdown(STATUS_CSS, unsafe_allow_html=True)

    st.caption("Better With Bees")
    st.title("StationWatch Live")

    controls, toggle = st.columns([1, 2], vertical_alignment="center")
    # The button needs no handler: any interaction reruns the script, and every
    # run downloads the Sheet again, so a refresh never shows cached data.
    controls.button("Refresh now", width="stretch")
    auto = toggle.toggle(f"Auto-refresh every {REFRESH_SECONDS}s", value=True)

    # Only the status panel repeats on a timer, and no faster than the station
    # samples. run_every=None leaves refreshing entirely manual.
    panel = st.fragment(render_check, run_every=REFRESH_SECONDS if auto else None)

    st.divider()
    panel()

    st.divider()
    render_observation_boundary()


main()
