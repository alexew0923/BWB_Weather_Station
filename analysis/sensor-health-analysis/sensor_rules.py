"""Transparent, configurable rules for historical sensor-health analysis."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class SensorRule:
    """One sensor's units, bounds, resolution, and event thresholds."""

    unit: str
    impossible_low: float
    impossible_high: float
    suspicious_low: float | None = None
    suspicious_high: float | None = None
    flatline_tolerance: float = 0.0
    flatline_min_samples: int = 12
    flatline_min_minutes: float = 55.0
    continuity_minutes: float = 15.0
    physical_rate_limit: float | None = None


# Impossible bounds are deliberately broad and match the reliability audit's
# established validation ranges. Suspicious pressure bounds are narrower but
# still broad for a near-sea-level Earth-surface station; they are reported, not
# removed. Raw ADC channels have no defensible environmental calibration here.
SENSOR_RULES = {
    "Temperature": SensorRule(
        "deg C", -50.0, 60.0, -35.0, 45.0,
        flatline_tolerance=0.02, flatline_min_samples=12,
        flatline_min_minutes=55.0,
    ),
    "Humidity": SensorRule(
        "% RH", 0.0, 100.0,
        flatline_tolerance=0.05, flatline_min_samples=12,
        flatline_min_minutes=55.0,
    ),
    "Soil Moisture": SensorRule(
        "raw ADC count", 0.0, 4095.0,
        flatline_tolerance=0.0, flatline_min_samples=6,
        flatline_min_minutes=150.0, continuity_minutes=45.0,
    ),
    "Air Pressure": SensorRule(
        "hPa", 800.0, 1100.0, 870.0, 1085.0,
        flatline_tolerance=0.05, flatline_min_samples=12,
        flatline_min_minutes=55.0, physical_rate_limit=5.0,
    ),
    "Rain Value": SensorRule(
        "raw wetness ADC count", 0.0, 4095.0,
        flatline_tolerance=0.0, flatline_min_samples=36,
        flatline_min_minutes=1440.0,
    ),
    # Battery is retained as context and in shared completeness, but the
    # dedicated battery-energy project remains authoritative for its behavior.
    "Battery Voltage": SensorRule(
        "mV", 0.0, 6000.0,
        flatline_tolerance=0.5, flatline_min_samples=24,
        flatline_min_minutes=115.0,
    ),
}

PRIMARY_SENSORS = (
    "Temperature", "Humidity", "Soil Moisture", "Air Pressure", "Rain Value",
)
ALL_SENSORS = PRIMARY_SENSORS + ("Battery Voltage",)

# Adjacent received rows farther apart than this are separate evidence runs.
# Thus a station outage cannot turn two isolated blanks into a long sensor run.
MISSING_RUN_CONTINUITY_MINUTES = 15.0

# Rates use actual UTC elapsed time and ignore sub-minute repeat transmissions.
MIN_RATE_INTERVAL_MINUTES = 1.0
STATISTICAL_RATE_QUANTILE = 0.999


@dataclass(frozen=True)
class HistoricalRegime:
    starts_on: date
    label: str
    evidence_note: str


# These boundaries describe interpretation changes documented in repository
# history. Git dates are not proof that a commit was deployed that same day, so
# every label keeps that uncertainty explicit.
HISTORICAL_REGIMES = (
    HistoricalRegime(
        date.min,
        "legacy ingestion / continuous schedule",
        "Pre-2025-12-16 export semantics; exact deployed revision is unverified.",
    ),
    HistoricalRegime(
        date(2025, 12, 16),
        "zero blanking added / continuous schedule",
        "Committed ingestion began blanking non-temperature zeros and removed a de-duplication guard; deployment date unverified.",
    ),
    HistoricalRegime(
        date(2026, 4, 5),
        "all-sensor zero blanking / continuous schedule",
        "Committed ingestion also blanked temperature zero; deployment date unverified.",
    ),
    HistoricalRegime(
        date(2026, 4, 21),
        "all-sensor zero blanking / 06:00-23:00 schedule",
        "Schedule boundary is empirically supported; ingestion deployment remains unverified.",
    ),
    HistoricalRegime(
        date(2026, 6, 19),
        "repository v2 code era / 06:00-23:00 schedule",
        "Repository code changed from periodic to every-transmission soil sampling around this date; actual deployment date is unverified.",
    ),
)


def historical_regime_for(day):
    """Return the latest documented interpretation regime for a local date."""
    chosen = HISTORICAL_REGIMES[0]
    for regime in HISTORICAL_REGIMES:
        if day >= regime.starts_on:
            chosen = regime
    return chosen


SEVERITY_RULES = {
    "minor": "isolated missing field, rain/wetness saturation flatline, or single-sensor signature",
    "significant": "multi-row missing run, suspicious value, statistical rate outlier, ordinary flatline, or multi-sensor signature",
    "critical": "physically impossible value/change, telemetry-wide populated-row anomaly, or flatline lasting at least 24 hours",
}
