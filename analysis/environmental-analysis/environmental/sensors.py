"""Canonical sensor names and the semantics attached to each of them.

The historical export uses the spreadsheet's own column headings. Two of those
headings claim more physical meaning than the hardware supports:

* ``Rain Value`` is the raw analog reading of a rain/wetness board. The project
  README states outright that it "does not actually measure the rainfall
  amount". It is a wetness *signal*, in ADC counts, not millimetres.
* ``Soil Moisture`` is the raw analog reading of an uncalibrated capacitive
  probe. No calibration curve exists anywhere in this repository, so it is a
  soil-moisture *signal*, in ADC counts, not volumetric water content.

Renaming them on the way in is the cheapest way to stop the wrong unit leaking
into every downstream calculation and, eventually, onto a web page. Every
canonical name in this module says what the number is, not what someone would
like it to mean.
"""

from dataclasses import dataclass


TIMESTAMP = "timestamp"

TEMPERATURE = "temperature_c"
HUMIDITY = "humidity_pct"
SOIL_SIGNAL = "soil_signal_raw"
PRESSURE = "pressure_hpa"
WETNESS_SIGNAL = "wetness_signal_raw"
BATTERY = "battery_mv"
BOOT_COUNT = "boot_count"

# Ordered oldest-to-newest in importance for this engine, not alphabetically:
# wetness drives event detection, soil drives response analysis, the rest is
# environmental context.
SENSOR_COLUMNS = (
    WETNESS_SIGNAL,
    SOIL_SIGNAL,
    TEMPERATURE,
    HUMIDITY,
    PRESSURE,
    BATTERY,
)

# Source heading -> canonical name. The source headings are those written by
# scripts/apps_script/doGet.js and mirrored in the reliability audit's
# SENSOR_COLUMNS.
SOURCE_COLUMN_MAP = {
    "Temperature": TEMPERATURE,
    "Humidity": HUMIDITY,
    "Soil Moisture": SOIL_SIGNAL,
    "Air Pressure": PRESSURE,
    "Rain Value": WETNESS_SIGNAL,
    "Battery Voltage": BATTERY,
    "Count": BOOT_COUNT,
}

# The timestamp heading differs between exports of the same spreadsheet: the
# HistoricalData tab exports as "Date" through /export?format=csv and as
# "Timestamp" through the gviz endpoint, and the live tab uses "Timestamp".
# Accept either, exactly as the reliability audit does.
TIMESTAMP_COLUMN_CANDIDATES = ("Date", "Timestamp")

# Sensors the engine cannot run at all without.
REQUIRED_SOURCE_COLUMNS = ("Rain Value",)


@dataclass(frozen=True)
class SensorSemantics:
    """What one canonical column is, and what it is not allowed to be called."""

    canonical_name: str
    source_name: str
    unit: str
    #: True when the number is a calibrated physical quantity in a real unit.
    calibrated: bool
    #: A statement of what the value legitimately means.
    means: str
    #: Statements the engine must never make about this value.
    does_not_mean: tuple


SEMANTICS = {
    WETNESS_SIGNAL: SensorSemantics(
        canonical_name=WETNESS_SIGNAL,
        source_name="Rain Value",
        unit="raw 12-bit ADC counts (0-4095)",
        calibrated=False,
        means=(
            "the analog output of a rain/wetness board, which falls below its "
            "dry rail while the sensing surface is conductive (wet)"
        ),
        does_not_mean=(
            "millimetres of rainfall",
            "precipitation rate or accumulated depth",
            "whether the water came from rain, dew, fog, snowmelt or irrigation",
        ),
    ),
    SOIL_SIGNAL: SensorSemantics(
        canonical_name=SOIL_SIGNAL,
        source_name="Soil Moisture",
        unit="raw 12-bit ADC counts (0-4095)",
        calibrated=False,
        means="the analog output of an uncalibrated capacitive soil probe",
        does_not_mean=(
            "volumetric water content",
            "percent soil moisture",
            "plant-available water or crop water deficit",
            "irrigation volume",
        ),
    ),
    TEMPERATURE: SensorSemantics(
        canonical_name=TEMPERATURE,
        source_name="Temperature",
        unit="degrees Celsius",
        calibrated=True,
        means="air temperature reported by the SHT40 inside the Stevenson screen",
        does_not_mean=("soil temperature", "leaf or canopy temperature"),
    ),
    HUMIDITY: SensorSemantics(
        canonical_name=HUMIDITY,
        source_name="Humidity",
        unit="percent relative humidity",
        calibrated=True,
        means="relative humidity reported by the SHT40",
        does_not_mean=("absolute humidity", "dew point", "precipitation"),
    ),
    PRESSURE: SensorSemantics(
        canonical_name=PRESSURE,
        source_name="Air Pressure",
        unit="hPa",
        calibrated=True,
        means="station-level air pressure reported by the BMP280",
        does_not_mean=(
            "sea-level-corrected pressure",
            "a forecast of any kind",
        ),
    ),
    BATTERY: SensorSemantics(
        canonical_name=BATTERY,
        source_name="Battery Voltage",
        unit="millivolts",
        calibrated=False,
        means=(
            "a divider-scaled battery voltage estimate; the battery-energy "
            "analysis project remains authoritative for it"
        ),
        does_not_mean=("state of charge", "remaining runtime"),
    ),
}


def semantics_metadata():
    """Return the sensor semantics as a serialisable structure."""
    return {
        name: {
            "source_name": item.source_name,
            "unit": item.unit,
            "calibrated": item.calibrated,
            "means": item.means,
            "does_not_mean": list(item.does_not_mean),
        }
        for name, item in SEMANTICS.items()
    }
