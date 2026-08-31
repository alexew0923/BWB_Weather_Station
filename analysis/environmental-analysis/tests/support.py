"""Synthetic telemetry builders shared by the test modules.

The station's real export is the contract these builders imitate: the source
column headings written by ``scripts/apps_script/doGet.js``, local wall-clock
timestamps with no UTC offset, and blank cells for missing readings.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from environmental.config import EnvironmentalConfig  # noqa: E402
from environmental.dataset import build_dataset_from_csv_text  # noqa: E402

HEADER = (
    "Date,Temperature,Humidity,Soil Moisture,Air Pressure,Rain Value,"
    "Battery Voltage,Count"
)

DRY_RAIL = 4095.0


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def row(moment, temperature=12.0, humidity=70.0, soil=1900.0, pressure=1010.0,
        wetness=DRY_RAIL, battery=4000.0, count=1):
    """One CSV line in the source's own format."""
    return ",".join(
        [
            moment.strftime("%Y-%m-%d %H:%M:%S"),
            _cell(temperature),
            _cell(humidity),
            _cell(soil),
            _cell(pressure),
            _cell(wetness),
            _cell(battery),
            _cell(count),
        ]
    )


def csv_text(rows):
    """Assemble a CSV document from prepared lines."""
    return "\n".join([HEADER, *rows]) + "\n"


def series_csv(start, count, interval_minutes=5.0, **overrides):
    """Build a regular telemetry series.

    Each keyword may be a constant or a callable taking ``(index, moment)``, so
    a test can shape one channel without restating the others.
    """
    lines = []
    for index in range(count):
        moment = start + timedelta(minutes=interval_minutes * index)
        values = {}
        for name, value in overrides.items():
            values[name] = value(index, moment) if callable(value) else value
        values.setdefault("count", index + 1)
        lines.append(row(moment, **values))
    return csv_text(lines)


def dataset_from(csv_document, config=None):
    """Build a canonical dataset from CSV text."""
    return build_dataset_from_csv_text(
        csv_document, config=config or EnvironmentalConfig()
    )


def wet_profile(dry_samples_before, wet_samples, dry_samples_after, depth=800.0):
    """A wetness channel that sits on the rail, dips, and returns.

    Returns a callable suitable for ``series_csv(wetness=...)``.
    """
    def value(index, _moment):
        if index < dry_samples_before:
            return DRY_RAIL
        if index < dry_samples_before + wet_samples:
            return DRY_RAIL - depth
        return DRY_RAIL
    return value


def humid_during(dry_samples_before, wet_samples, wet_humidity=97.0,
                 dry_humidity=60.0):
    """Humidity that corroborates a wetting window."""
    def value(index, _moment):
        if dry_samples_before <= index < dry_samples_before + wet_samples:
            return wet_humidity
        return dry_humidity
    return value


START = datetime(2026, 5, 14, 6, 0, 0)
