"""Historical outage forensics for the unified station monitor."""

import pandas as pd
import streamlit as st

from services import incident_service


HISTORICAL_SOURCE = incident_service.resolve_historical_source()
DATA_PATH = HISTORICAL_SOURCE["local_path"]
HISTORICAL_DATA_URL = HISTORICAL_SOURCE["remote_url"]


@st.cache_data(max_entries=8, show_spinner=False)
def cached_load_local_incidents(csv_path, fingerprint):
    """Cache validated local history and deterministic outage detection."""
    del fingerprint  # File metadata exists solely to invalidate this cache entry.
    return incident_service.load_incident_catalog(csv_path)


@st.cache_data(ttl=600, max_entries=4, show_spinner=False)
def cached_load_remote_incidents(url):
    """Fetch and analyze remote history with the dashboard's ten-minute TTL."""
    csv_text = incident_service.fetch_historical_csv(url)
    return incident_service.load_incident_catalog_from_csv_text(csv_text)


def format_hours(hours):
    if hours is None or pd.isna(hours):
        return "Not available"
    total_minutes = int(round(float(hours) * 60))
    return f"{total_minutes // 60}h {total_minutes % 60:02d}m"


def format_timestamp(value, compact=False):
    if value is None or pd.isna(value):
        return "Not available"
    timestamp = pd.Timestamp(value)
    pattern = "%d %b %Y · %H:%M" if not compact else "%Y-%m-%d %H:%M"
    return timestamp.strftime(pattern)


def format_percent(value):
    if value is None or pd.isna(value):
        return "No scheduled opportunities"
    return f"{100 * float(value):.1f}%"


def format_voltage(value):
    if value is None or pd.isna(value):
        return "Not available"
    return f"{float(value):.3f} V"


def incident_label(row):
    start = pd.Timestamp(row["gap_start"])
    end = pd.Timestamp(row["gap_end"])
    return (
        f"{start:%Y-%m-%d %H:%M} → {end:%Y-%m-%d %H:%M} · "
        f"{format_hours(row['gap_hours'])} · {str(row['severity']).title()}"
    )


def evidence_list(items):
    for item in items:
        st.markdown(f"- {item}")


def sensor_findings(summary):
    """Select existing incident-engine flags for compact presentation."""
    findings = []
    for sensor, values in summary.items():
        if sensor == "Battery Voltage":
            continue
        if values["status"] != "available":
            findings.append(f"{sensor}: {values['status']}.")
        elif values["implausible_values"]:
            findings.append(
                f"{sensor}: {values['implausible_values']} implausible value(s)."
            )
        elif values["completeness"] is not None and values["completeness"] < 1:
            findings.append(
                f"{sensor}: {100 * values['completeness']:.1f}% populated."
            )
    return findings


def methodology(validation_log=None):
    with st.expander("Methodology & limitations"):
        st.markdown(
            "A **significant outage** is a moderate, major, or critical gap under "
            "the reliability audit's existing definitions. Scheduled overnight "
            "shutdowns, nominal cadence, and sub-minute repeats are not promoted "
            "to significant outages."
        )
        st.markdown(
            "Expected transmissions use the audit's operating schedule. Intentionally "
            "inactive time is excluded, elapsed time is evaluated with "
            "America/Halifax timezone and DST-aware timestamps, and detected gap "
            "boundary rows are excluded from the incident interval."
        )
        st.markdown(
            "Missing delivery telemetry is not proof that the outdoor node or any "
            "specific component failed. Current instrumentation cannot distinguish "
            "sensor, transmitter, power, ESP-NOW, receiver, I2C bridge, Wi-Fi, Apps "
            "Script, or Google Sheets failure domains. Battery evidence is contextual, "
            "not causal, and is never converted to state of charge."
        )
        if validation_log:
            with st.expander("Validation trace"):
                st.code(
                    incident_service.safe_historical_error_detail(
                        validation_log.strip(), HISTORICAL_SOURCE
                    ),
                    language=None,
                )


st.html('<div class="bwb-kicker">Better With Bees · Historical reliability</div>')
st.title("Incident Explorer")
st.html(
    '<p class="bwb-deck">Outage forensics and recovery analysis. Select a detected '
    'significant gap to separate observed evidence from plausible—but unproven—causes.</p>'
)
with st.container(horizontal=True, gap="small"):
    st.badge("Detected outages", icon=":material/timeline:", color="red")
    st.badge("Schedule-aware", icon=":material/schedule:", color="blue")
    st.badge("Evidence-bounded", icon=":material/fact_check:", color="green")
if HISTORICAL_SOURCE["display_label"]:
    st.caption(f"Historical source: **{HISTORICAL_SOURCE['display_label']}**")

if DATA_PATH is not None and not DATA_PATH.exists():
    st.error("The historical telemetry CSV could not be found.", icon=":material/error:")
    st.caption(
        "Set BWB_HISTORICAL_CSV to a readable HistoricalData.csv path and restart the app."
    )
    st.stop()

if DATA_PATH is None and not HISTORICAL_DATA_URL:
    st.error("Historical telemetry is not configured.", icon=":material/settings_alert:")
    st.caption(
        f"Set {incident_service.HISTORICAL_DATA_URL_VARIABLE} to a public Google Sheets "
        "edit or CSV export URL."
    )
    st.stop()

try:
    with st.spinner("Validating telemetry and detecting significant outages…"):
        if DATA_PATH is not None:
            source_reference = str(DATA_PATH.resolve())
            catalog = cached_load_local_incidents(
                source_reference, incident_service.file_fingerprint(DATA_PATH)
            )
        else:
            source_reference = incident_service.normalize_historical_data_url(
                HISTORICAL_DATA_URL
            )
            catalog = cached_load_remote_incidents(source_reference)
except incident_service.HistoricalDataError as error:
    st.error(error.summary, icon=":material/cloud_off:")
    st.caption(f"Details: {error.detail}")
    st.caption(
        f"Set {incident_service.HISTORICAL_DATA_URL_VARIABLE} to a public Google Sheets "
        "CSV export and retry."
    )
    st.stop()
except (SystemExit, ValueError, KeyError, OSError, pd.errors.ParserError) as error:
    st.error("The telemetry source could not be analyzed safely.", icon=":material/error:")
    st.caption(
        f"Details: {incident_service.safe_historical_error_detail(error, HISTORICAL_SOURCE)}"
    )
    st.caption("The source was not modified. Correct the input and restart the app.")
    st.stop()

incidents = catalog["incidents"]
if incidents.empty:
    st.info("No significant outages were detected for the selected period.")
    methodology(catalog["validation_log"])
    st.stop()

earliest = pd.Timestamp(incidents["gap_start"].min()).date()
latest = pd.Timestamp(incidents["gap_start"].max()).date()
severities = sorted(incidents["severity"].astype(str).unique().tolist())

st.subheader("Detected incidents")
st.caption(
    "Filter the audit's detected significant outages, then select one for focused analysis."
)
with st.container(border=True):
    filter_date, filter_duration, filter_severity = st.columns(3, gap="medium")
    with filter_date:
        selected_dates = st.date_input(
            "Incident date range",
            value=(earliest, latest),
            min_value=earliest,
            max_value=latest,
            format="YYYY-MM-DD",
            key="incidents_date_range",
        )
    with filter_duration:
        minimum_hours = st.number_input(
            "Minimum duration (hours)",
            min_value=0.0,
            value=0.0,
            step=1.0,
            key="incidents_minimum_hours",
        )
    with filter_severity:
        selected_severities = st.multiselect(
            "Severity",
            severities,
            default=severities,
            key="incidents_severity",
        )

filtered = incidents.copy()
if isinstance(selected_dates, (tuple, list)) and len(selected_dates) == 2:
    start_date, end_date = selected_dates
    incident_dates = filtered["gap_start"].dt.date
    filtered = filtered[(incident_dates >= start_date) & (incident_dates <= end_date)]
filtered = filtered[filtered["gap_hours"] >= minimum_hours]
filtered = filtered[filtered["severity"].isin(selected_severities)]
filtered = filtered.sort_values("gap_start", ascending=False)

if filtered.empty:
    st.info("No significant outages were detected for the selected period.")
    methodology(catalog["validation_log"])
    st.stop()

incident_ids = filtered["incident_id"].astype(int).tolist()
selected_id = st.selectbox(
    "Incident",
    incident_ids,
    format_func=lambda identifier: incident_label(
        incidents.loc[incidents["incident_id"] == identifier].iloc[0]
    ),
    key="incident_selection",
)
selected = incidents.loc[incidents["incident_id"] == selected_id].iloc[0]

try:
    report = incident_service.analyze_selected_incident(
        catalog["frame"],
        selected.to_dict(),
        source=HISTORICAL_SOURCE["display_label"],
    )
except (SystemExit, ValueError, KeyError, OSError) as error:
    st.error("The selected incident could not be analyzed.", icon=":material/error:")
    st.caption(f"Details: {error}")
    report = None

if report is not None:
    incident = report["incident"]
    pre = report["pre_window"]
    post = report["post_window"]

    st.divider()
    st.subheader("Selected incident")
    st.caption(
        f"{format_timestamp(incident['start'])} → {format_timestamp(incident['end'])} · "
        f"{str(selected['severity']).title()}"
    )
    with st.container(horizontal=True, wrap=True, gap="small"):
        st.metric("Duration", format_hours(incident["duration_hours"]), border=True, width=210)
        st.metric(
            "Expected transmissions",
            f"{incident['expected_readings']:,}",
            help="Schedule-aware missed opportunities between the received boundary rows.",
            border=True,
            width=235,
        )
        st.metric("Received during outage", f"{incident['received_readings']:,}", border=True, width=235)
        st.metric("Pre-incident completeness", format_percent(pre["telemetry_completeness"]), border=True, width=245)
        st.metric("Recovery delay", format_hours(post["recovery_delay_hours"]), border=True, width=210)

    st.subheader("Before · during · recovery")
    before_column, during_column, recovery_column = st.columns(3, gap="medium")
    with before_column:
        with st.container(border=True, height="stretch"):
            st.markdown("### Before")
            st.caption(f"{format_timestamp(pre['start'], True)} → {format_timestamp(pre['end'], True)}")
            st.metric("Telemetry completeness", format_percent(pre["telemetry_completeness"]))
            st.write(f"Typical inter-arrival: **{pre['typical_interarrival_minutes'] or 'Not available'} min**")
            st.write(f"Minor / significant gaps: **{pre['minor_gaps']} / {pre['significant_gaps']}**")
            st.write(f"Repeats / long gaps: **{pre['repeat_transmissions']} / {pre['irregular_gaps']}**")
    with during_column:
        with st.container(border=True, height="stretch"):
            st.markdown("### During")
            st.caption(f"{format_timestamp(incident['start'], True)} → {format_timestamp(incident['end'], True)}")
            st.metric("Expected / received", f"{incident['expected_readings']} / {incident['received_readings']}")
            st.write(f"Schedulable time: **{format_hours(incident['schedulable_hours'])}**")
            st.write(f"Scheduled inactive: **{format_hours(incident['scheduled_inactive_hours'])}**")
            overlap = "Yes" if incident["overlaps_scheduled_inactive_period"] else "No"
            st.write(f"Crosses inactive schedule: **{overlap}**")
    with recovery_column:
        with st.container(border=True, height="stretch"):
            st.markdown("### Recovery")
            st.caption(f"{format_timestamp(post['start'], True)} → {format_timestamp(post['end'], True)}")
            st.metric("First valid telemetry", format_timestamp(post["first_reading_after_incident"], True))
            st.write(f"Recovery delay: **{format_hours(post['recovery_delay_hours'])}**")
            st.write(f"Subsequent completeness: **{format_percent(post['telemetry_completeness'])}**")
            st.write(f"Repeats / long gaps: **{post['repeat_transmissions']} / {post['irregular_gaps']}**")

    st.subheader("Forensic timeline")
    st.caption(
        "Each dark mark is a received historical row. The red interval is the selected "
        "detected gap; orange voltage appears only where battery telemetry is available."
    )
    try:
        plot_bytes = incident_service.render_incident_plot(catalog["frame"], report)
        st.image(plot_bytes, width="stretch")
        st.caption(
            f"Before: {format_timestamp(pre['start'], True)} → {format_timestamp(pre['end'], True)} · "
            f"Incident: {format_timestamp(incident['start'], True)} → {format_timestamp(incident['end'], True)} · "
            f"Recovery: {format_timestamp(post['start'], True)} → {format_timestamp(post['end'], True)}"
        )
    except (ValueError, OSError, RuntimeError) as error:
        st.warning("The incident report is available, but its timeline could not be rendered.")
        st.caption(f"Details: {error}")

    st.subheader("Evidence assessment")
    observed_column, suggestive_column, unknown_column = st.columns(3, gap="medium")
    with observed_column:
        with st.container(border=True, height="stretch"):
            st.markdown("### Observed")
            evidence_list(report["interpretation"]["observed"])
    with suggestive_column:
        with st.container(border=True, height="stretch"):
            st.markdown("### Suggestive")
            evidence_list(report["interpretation"]["suggestive"])
    with unknown_column:
        with st.container(border=True, height="stretch"):
            st.markdown("### Not determinable")
            evidence_list(report["interpretation"]["not_determinable"])

    st.subheader("Battery context")
    pre_battery = pre["battery"]
    post_battery = post["battery"]
    if pre_battery["first_volts"] is None and post_battery["first_volts"] is None:
        st.info("Battery telemetry unavailable for this incident.")
    else:
        with st.container(horizontal=True, wrap=True, gap="small"):
            st.metric("Last pre-outage voltage", format_voltage(pre_battery["last_volts"]), border=True, width=235)
            st.metric("Pre-window voltage change", format_voltage(pre_battery["change_volts"]), border=True, width=245)
            trend = pre_battery["trend_volts_per_hour"]
            trend_value = "Not available" if trend is None else f"{trend:+.4f} V/h"
            st.metric("Pre-window voltage trend", trend_value, border=True, width=245)
            st.metric("First recovery voltage", format_voltage(post_battery["first_volts"]), border=True, width=235)
        st.caption(
            f"Before: {pre_battery['status']}. Recovery: {post_battery['status']}. "
            "Voltage context does not establish battery state of charge or outage cause."
        )

    st.subheader("Sensor context")
    before_findings = sensor_findings(pre["sensors"])
    recovery_findings = sensor_findings(post["sensors"])
    before_sensor, recovery_sensor = st.columns(2, gap="medium")
    with before_sensor:
        with st.container(border=True):
            st.markdown("**Before**")
            if before_findings:
                evidence_list(before_findings)
            else:
                st.write("All commissioned fields were populated; no implausible values were flagged.")
    with recovery_sensor:
        with st.container(border=True):
            st.markdown("**Recovery**")
            if recovery_findings:
                evidence_list(recovery_findings)
            else:
                st.write("All commissioned fields were populated; no implausible values were flagged.")

st.subheader("Incident list")
table = filtered[[
    "gap_start",
    "gap_end",
    "gap_hours",
    "severity",
    "missed_transmissions",
    "count_before",
    "count_after",
]].rename(columns={
    "gap_start": "Start",
    "gap_end": "End",
    "gap_hours": "Duration (hours)",
    "severity": "Severity",
    "missed_transmissions": "Missed transmissions",
    "count_before": "Count before",
    "count_after": "Count after",
})
st.dataframe(
    table,
    hide_index=True,
    column_config={
        "Start": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm"),
        "End": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm"),
        "Duration (hours)": st.column_config.NumberColumn(format="%.2f"),
        "Severity": st.column_config.TextColumn(),
        "Missed transmissions": st.column_config.NumberColumn(format="%d"),
    },
    width="stretch",
)
st.caption(
    f"Showing {len(filtered):,} of {len(incidents):,} significant outages · "
    f"source: {HISTORICAL_SOURCE['display_label']}"
)

methodology(catalog["validation_log"])
st.caption(
    "Better With Bees Incident Explorer · America/Halifax local time · "
    "historical delivery evidence, not component-level root-cause proof."
)
