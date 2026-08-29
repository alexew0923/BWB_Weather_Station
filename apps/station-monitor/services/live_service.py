"""Thin presentation adapter around the StationWatch analysis engine."""

from services import analysis_imports as _analysis_imports  # noqa: F401
from station_health import (  # noqa: E402
    OBSERVATION_NOTE,
    UPSTREAM_DOMAINS,
    HealthReport,
    MonitorError,
    StationMonitor,
    Status,
    format_timestamp,
)


DISPLAY_FIELDS = (
    "Temperature",
    "Humidity",
    "Soil Moisture",
    "Air Pressure",
    "Rain Value",
    "Battery Voltage",
)


def load_live_report():
    """Perform one uncached live Sheet check through StationWatch."""
    return StationMonitor().check()


def meaningful_latest_values(report):
    """Return configured live fields that were populated in the newest row."""
    values = dict(report.latest_values)
    return [(name, values[name]) for name in DISPLAY_FIELDS if values.get(name)]


def latest_battery_value(report):
    """Return the newest source battery value exactly as reported, if present."""
    return dict(report.latest_values).get("Battery Voltage") or None
