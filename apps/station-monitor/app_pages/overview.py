"""Sparse landing page for the environmental monitoring system."""

import streamlit as st

from components.metric_cards import summary_metrics
from services.live_service import (
    MonitorError,
    format_timestamp,
    latest_battery_value,
    load_live_report,
)


@st.cache_data(ttl=30, max_entries=2, show_spinner=False)
def cached_live_report():
    """Keep overview reruns responsive without obscuring operational freshness."""
    return load_live_report()


st.html('<div class="bwb-kicker">Better With Bees</div>')
st.title("Environmental monitoring system")
st.html(
    '<p class="bwb-deck">One operational view of live telemetry delivery and '
    'one research view of historical battery and energy behavior.</p>'
)

try:
    report = cached_live_report()
except MonitorError as error:
    summary_metrics(
        [
            ("Station status", "MONITOR ERROR", error.summary),
            ("Last telemetry", "Not available", "The live source could not be read."),
            ("Latest battery", "Not available", "No usable live row is available."),
        ]
    )
    st.caption(
        "Live source unavailable. Historical battery analysis remains independent and usable."
    )
else:
    summary_metrics(
        [
            ("Station status", str(report.status), report.summary),
            (
                "Last telemetry",
                format_timestamp(report.latest_timestamp),
                "Newest valid timestamp at the live Google Sheets observation point.",
            ),
            (
                "Latest battery",
                latest_battery_value(report) or "Not available",
                "Newest value reported by the live Sheet; no historical substitution.",
            ),
        ]
    )

st.space("medium")
live_col, battery_col = st.columns(2, gap="large")
with live_col:
    with st.container(border=True):
        st.subheader("Live monitoring")
        st.write("Operational station state, telemetry freshness, and recent arrivals.")
        st.page_link(
            "app_pages/live.py",
            label="Open live monitor",
            icon=":material/arrow_forward:",
        )
with battery_col:
    with st.container(border=True):
        st.subheader("Battery & energy analysis")
        st.write("Historical power-system behavior, reliability context, and research limits.")
        st.page_link(
            "app_pages/battery.py",
            label="Open battery analysis",
            icon=":material/arrow_forward:",
        )

st.caption(
    "Live monitoring reads the configured Google Sheet. Battery analysis reads the repository's historical CSV."
)
