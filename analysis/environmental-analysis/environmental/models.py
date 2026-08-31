"""The domain model: what this engine is allowed to say, and how strongly.

The single most important idea in this module is the separation between four
different kinds of statement:

``RAW_OBSERVATION``
    A number the station actually reported. "The wetness signal read 3 210."

``COMPUTED_MEASUREMENT``
    Arithmetic on observations. "The wetness signal was 885 counts below its
    dry reference."

``STATISTICAL_EVIDENCE``
    A statement about a distribution. "That deviation is larger than 99% of all
    deviations in the record."

``INTERPRETATION``
    What a person may reasonably conclude. "A probable environmental wetting
    event occurred."

Every conclusion this engine emits carries the evidence it rests on, so a
frontend can show the reasoning rather than a bare verdict, and so an
unsupported claim has nowhere to hide.

All models are frozen dataclasses with a ``to_dict`` that produces JSON-ready
primitives. Consumers never receive a DataFrame.
"""

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date, datetime
from enum import Enum

import math


# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------


class StatementKind(Enum):
    """Which of the four kinds of statement a piece of evidence is."""

    RAW_OBSERVATION = "raw_observation"
    COMPUTED_MEASUREMENT = "computed_measurement"
    STATISTICAL_EVIDENCE = "statistical_evidence"
    INTERPRETATION = "interpretation"

    def __str__(self):
        return self.value


class EvidenceStrength(Enum):
    """How well supported a conclusion is.

    Deliberately ordinal and non-numeric. This engine has no calibrated
    probability model, so emitting "87% confidence" would be an invented
    number dressed up as a measurement.
    """

    OBSERVED = "observed"       # directly measured, no inference at all
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"
    INSUFFICIENT = "insufficient"

    def __str__(self):
        return self.value


class DataQuality(Enum):
    """Whether data may be interpreted, and how far."""

    USABLE = "usable"
    PARTIALLY_USABLE = "partially_usable"
    INSUFFICIENT = "insufficient"
    INVALID = "invalid"

    def __str__(self):
        return self.value

    @property
    def supports_negative_conclusion(self):
        """True when "we looked and there was nothing" is defensible.

        Absence of evidence is only evidence of absence when the data was good
        enough to have shown the thing had it happened.
        """
        return self is DataQuality.USABLE


class EventType(Enum):
    """The kinds of event this engine detects.

    One member today. The enum exists so that adding, say, a drying event later
    does not require changing every consumer's parsing.
    """

    WETTING = "wetting"

    def __str__(self):
        return self.value


class EventClassification(Enum):
    """How confidently a detected interval may be described.

    There is deliberately no ``heavy_rainfall`` or ``light_rain``: the sensor is
    uncalibrated, so no intensity class is defensible.
    """

    PROBABLE_WETTING_EVENT = "probable_wetting_event"
    CANDIDATE_WETTING_EVENT = "candidate_wetting_event"
    UNCERTAIN_WETTING_EVENT = "uncertain_wetting_event"

    def __str__(self):
        return self.value


class SoilResponseStatus(Enum):
    """Whether the soil signal responded to a wetting event."""

    DETECTED = "DETECTED"
    NOT_DETECTED = "NOT_DETECTED"
    UNKNOWN = "UNKNOWN"

    def __str__(self):
        return self.value


class SignalDirection(Enum):
    """Direction of a raw-signal change, with no wetness meaning attached.

    The soil probe's polarity is not documented anywhere in this repository and
    is not established by the data, so the engine reports "the signal rose" or
    "the signal fell" and refuses to translate that into "wetter" or "drier".
    """

    INCREASE = "increase"
    DECREASE = "decrease"
    NONE = "none"
    UNDETERMINED = "undetermined"

    def __str__(self):
        return self.value


class AnomalyKind(Enum):
    """Extensible anomaly taxonomy. Sensor faults are deliberately absent."""

    HIGH_TEMPERATURE = "unusually_high_temperature"
    LOW_TEMPERATURE = "unusually_low_temperature"
    HIGH_HUMIDITY = "unusually_high_humidity"
    LOW_HUMIDITY = "unusually_low_humidity"
    RAPID_TEMPERATURE_CHANGE = "unusually_rapid_temperature_change"
    PERSISTENT_WETNESS = "unusually_persistent_wetness"
    UNUSUAL_SOIL_SIGNAL = "unusual_soil_signal_behaviour"

    def __str__(self):
        return self.value


class FreshnessState(Enum):
    """What is known about the newest telemetry, when anything is."""

    CURRENT = "current"
    STALE = "stale"
    AWAITING_TELEMETRY = "awaiting_telemetry"
    INSUFFICIENT_DATA = "insufficient_data"
    UNAVAILABLE = "unavailable"

    def __str__(self):
        return self.value


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------


def to_serialisable(value):
    """Convert domain objects into JSON-ready primitives.

    Handles dataclasses, enums, datetimes, mappings, sequences and NaN. NaN is
    rendered as ``None`` on purpose: it is not a number, and a frontend that
    receives ``NaN`` in JSON has to special-case a value that is not valid JSON
    in the first place.
    """
    if value is None:
        return None
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: to_serialisable(getattr(value, item.name))
            for item in fields(value)
            if item.metadata.get("serialise", True)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): to_serialisable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_serialisable(item) for item in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return value
    # NumPy scalars and pandas Timestamps both implement one of these.
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        return to_serialisable(value.item())
    return str(value)


class Serialisable:
    """Mixin giving every domain object the same ``to_dict``."""

    def to_dict(self):
        return to_serialisable(self)


# --------------------------------------------------------------------------
# Evidence and quality
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Evidence(Serialisable):
    """One statement, labelled with what kind of statement it is."""

    kind: StatementKind
    statement: str
    quantity: str | None = None
    value: float | None = None
    unit: str | None = None


@dataclass(frozen=True)
class QualityAssessment(Serialisable):
    """A data-quality verdict that always explains itself."""

    level: DataQuality
    reasons: tuple = ()
    observations: int = 0
    valid_observations: int = 0
    expected_observations: int | None = None
    telemetry_coverage: float | None = None
    longest_gap_minutes: float | None = None

    @property
    def usable(self):
        return self.level is DataQuality.USABLE

    def with_reason(self, reason):
        return QualityAssessment(
            level=self.level,
            reasons=tuple(self.reasons) + (reason,),
            observations=self.observations,
            valid_observations=self.valid_observations,
            expected_observations=self.expected_observations,
            telemetry_coverage=self.telemetry_coverage,
            longest_gap_minutes=self.longest_gap_minutes,
        )


@dataclass(frozen=True)
class Interpretation(Serialisable):
    """What may be concluded, kept separate from what was measured."""

    statement: str
    evidence_strength: EvidenceStrength
    #: Never guessed. Attributing a wetting event to rain rather than dew, fog
    #: or snowmelt needs evidence this station does not collect.
    cause: str = "undetermined"
    caveats: tuple = ()


# --------------------------------------------------------------------------
# Descriptive statistics
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SignalStatistics(Serialisable):
    """Robust descriptive statistics for one signal over one window."""

    count: int = 0
    valid_count: int = 0
    minimum: float | None = None
    maximum: float | None = None
    mean: float | None = None
    median: float | None = None
    p10: float | None = None
    p25: float | None = None
    p75: float | None = None
    p90: float | None = None
    #: Median absolute deviation scaled to a standard-deviation equivalent.
    robust_sigma: float | None = None
    standard_deviation: float | None = None


@dataclass(frozen=True)
class WindowComparison(Serialisable):
    """One context sensor compared across the pre-event and event windows."""

    sensor: str
    unit: str
    pre_event: SignalStatistics = field(default_factory=SignalStatistics)
    during_event: SignalStatistics = field(default_factory=SignalStatistics)
    post_event: SignalStatistics = field(default_factory=SignalStatistics)
    change_from_baseline: float | None = None
    post_event_change: float | None = None
    quality: QualityAssessment | None = None


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class WetnessObservations(Serialisable):
    """Measured properties of the wetness excursion itself.

    Every field is in raw ADC counts or minutes. None of them is a depth, a
    rate, or a volume.
    """

    dry_reference_counts: float | None = None
    minimum_signal_counts: float | None = None
    peak_deviation_counts: float | None = None
    mean_deviation_counts: float | None = None
    median_deviation_counts: float | None = None
    #: Deviation integrated over time. Units are count-minutes, which is a
    #: property of this sensor's output and NOT a proxy for accumulated water.
    integrated_deviation_count_minutes: float | None = None
    time_to_peak_minutes: float | None = None
    onset_rate_counts_per_minute: float | None = None
    recovery_rate_counts_per_minute: float | None = None
    samples: int = 0
    duration_minutes: float = 0.0


@dataclass(frozen=True)
class SoilResponse(Serialisable):
    """The soil-signal verdict for one event.

    ``status`` is ``NOT_DETECTED`` only when the data was good enough that a
    response would have been visible. Otherwise it is ``UNKNOWN``.
    """

    status: SoilResponseStatus
    quality: QualityAssessment
    direction: SignalDirection = SignalDirection.UNDETERMINED
    baseline_counts: float | None = None
    baseline_sigma_counts: float | None = None
    detection_threshold_counts: float | None = None
    #: Signed change in raw ADC counts, relative to the pre-event baseline.
    response_counts: float | None = None
    #: The same change divided by the baseline level. Dimensionless, and still
    #: not a water content.
    relative_change: float | None = None
    delay_minutes: float | None = None
    peak_counts: float | None = None
    time_to_peak_minutes: float | None = None
    persistence_minutes: float | None = None
    diurnal_adjusted: bool = False
    baseline_samples: int = 0
    response_samples: int = 0
    ambiguous_zero_fraction: float | None = None
    evidence: tuple = ()
    version: str = ""


@dataclass(frozen=True)
class ModelFit(Serialisable):
    """One empirical curve fitted to a post-event trajectory.

    An empirical description of the observed shape. It is not a claim about the
    physical mechanism of soil drainage.
    """

    name: str
    formula: str
    parameters: dict = field(default_factory=dict)
    samples: int = 0
    mae: float | None = None
    rmse: float | None = None
    r_squared: float | None = None
    aic: float | None = None
    accepted: bool = False
    rejection_reason: str | None = None


@dataclass(frozen=True)
class PostEventDynamics(Serialisable):
    """Descriptive post-event trajectory of the soil signal."""

    quality: QualityAssessment
    samples: int = 0
    window_minutes: float | None = None
    peak_counts: float | None = None
    time_to_peak_minutes: float | None = None
    end_counts: float | None = None
    change_from_peak_counts: float | None = None
    fraction_recovered: float | None = None
    time_to_half_recovery_minutes: float | None = None
    median_rate_counts_per_hour: float | None = None
    returned_to_baseline: bool | None = None
    models: tuple = ()
    version: str = ""


@dataclass(frozen=True)
class EventBoundaries(Serialisable):
    """How well the start and end of an event were actually observed.

    A wetting event that begins while the station is powered down is observed
    from the moment power returns, not from the moment it started. Saying so is
    the difference between a measurement and a guess.
    """

    start_censored: bool = False
    end_censored: bool = False
    gap_before_minutes: float | None = None
    gap_after_minutes: float | None = None
    largest_internal_gap_minutes: float | None = None


@dataclass(frozen=True)
class EnvironmentalEvent(Serialisable):
    """One detected environmental event and everything known about it."""

    event_id: str
    event_type: EventType
    station_id: str
    start_time: datetime
    end_time: datetime
    duration_minutes: float
    classification: EventClassification
    interpretation: Interpretation
    observations: WetnessObservations
    boundaries: EventBoundaries
    data_quality: QualityAssessment
    soil_response: SoilResponse
    post_event_dynamics: PostEventDynamics | None = None
    context: dict = field(default_factory=dict)
    evidence: tuple = ()
    warnings: tuple = ()
    detection_method: str = ""
    detection_version: str = ""
    engine_version: str = ""

    @property
    def evidence_strength(self):
        """Shorthand for the interpretation's strength."""
        return self.interpretation.evidence_strength


# --------------------------------------------------------------------------
# Profiling, baselines, anomalies, current state
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SensorProfile(Serialisable):
    """Exploratory profile of one sensor across the whole record."""

    sensor: str
    unit: str
    calibrated: bool
    observations: int
    valid_observations: int
    valid_fraction: float
    statistics: SignalStatistics
    quality: QualityAssessment
    #: Free-form, sensor-specific findings, all of them measurements.
    findings: dict = field(default_factory=dict)
    notes: tuple = ()


@dataclass(frozen=True)
class DiurnalPoint(Serialisable):
    """One hour-of-day bucket in a diurnal profile."""

    hour: int
    samples: int
    median: float | None = None
    p10: float | None = None
    p90: float | None = None


@dataclass(frozen=True)
class PeriodBaseline(Serialisable):
    """Baseline statistics for one calendar period, with its coverage."""

    period: str
    start_time: datetime
    end_time: datetime
    observations: int
    expected_observations: int | None
    telemetry_coverage: float | None
    evidence_strength: EvidenceStrength
    sensors: dict = field(default_factory=dict)
    wetness_event_count: int | None = None
    wetness_event_hours: float | None = None
    notes: tuple = ()


@dataclass(frozen=True)
class EnvironmentalBaseline(Serialisable):
    """Baseline characterisation of the whole record."""

    station_id: str
    start_time: datetime
    end_time: datetime
    periods: tuple = ()
    diurnal: dict = field(default_factory=dict)
    daily_ranges: dict = field(default_factory=dict)
    wetness_events: dict = field(default_factory=dict)
    quality: QualityAssessment | None = None
    version: str = ""


@dataclass(frozen=True)
class EnvironmentalAnomaly(Serialisable):
    """One anomaly, with the evidence that makes it unusual."""

    anomaly_id: str
    kind: AnomalyKind
    sensor: str
    time: datetime
    value: float | None
    baseline_median: float | None
    robust_z: float | None
    percentile: float | None
    history_samples: int
    evidence_strength: EvidenceStrength
    quality: QualityAssessment
    evidence: tuple = ()
    interpretation: Interpretation | None = None
    version: str = ""


@dataclass(frozen=True)
class SensorReading(Serialisable):
    """One current value with its own freshness."""

    sensor: str
    unit: str
    value: float | None
    observed_at: datetime | None
    calibrated: bool
    recent: SignalStatistics | None = None
    quality: QualityAssessment | None = None


@dataclass(frozen=True)
class CurrentEnvironmentalState(Serialisable):
    """The newest defensible statement about conditions at the station.

    When telemetry is missing, stale or unreadable this object says exactly
    that. It never substitutes older data and calls it "current".
    """

    station_id: str
    freshness: FreshnessState
    as_of: datetime | None = None
    latest_observation_at: datetime | None = None
    data_age_minutes: float | None = None
    source_label: str = ""
    readings: dict = field(default_factory=dict)
    active_wetness: dict = field(default_factory=dict)
    recent_events: tuple = ()
    anomalies: tuple = ()
    quality: QualityAssessment | None = None
    summary: str = ""
    engine_version: str = ""


@dataclass(frozen=True)
class EnvironmentalSummary(Serialisable):
    """The top-level result of a full analysis run."""

    station_id: str
    generated_at: datetime
    source: dict = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)
    profiles: dict = field(default_factory=dict)
    events: tuple = ()
    event_counts: dict = field(default_factory=dict)
    soil_response_counts: dict = field(default_factory=dict)
    baseline: EnvironmentalBaseline | None = None
    anomalies: tuple = ()
    quality: QualityAssessment | None = None
    limitations: tuple = ()
    versions: dict = field(default_factory=dict)
