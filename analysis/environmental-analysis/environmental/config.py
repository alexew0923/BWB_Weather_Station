"""Centralised, overridable configuration for the environmental engine.

Every threshold, window and tolerance the analysis uses lives here. Nothing
downstream is allowed to invent a constant of its own, because a parameter that
is not in this file cannot be reviewed, documented or changed by a caller.

All configuration objects are frozen dataclasses, so a configuration cannot be
mutated behind an analysis that has already run. Override with
``dataclasses.replace``::

    from dataclasses import replace
    config = replace(
        EnvironmentalConfig(),
        wetness=replace(EnvironmentalConfig().wetness, enter_counts=50.0),
    )

The justification for each default is in the README under "Parameter
justification". Where a default was chosen from this dataset rather than from
physics, the docstring says so.
"""

from dataclasses import dataclass, field, replace

from . import sensors
from ._reliability_bridge import (
    NOMINAL_CYCLE_MINUTES,
    PLAUSIBLE_SENSOR_RANGES,
    STATION_TIMEZONE,
)


# Physically plausible ranges, keyed by canonical column name. The values come
# from the reliability audit so that "impossible" means the same thing in every
# project in this repository.
PLAUSIBLE_RANGES = {
    sensors.SOURCE_COLUMN_MAP[source]: bounds
    for source, bounds in PLAUSIBLE_SENSOR_RANGES.items()
    if source in sensors.SOURCE_COLUMN_MAP
}


@dataclass(frozen=True)
class IngestionConfig:
    """How remote telemetry is retrieved and turned into rows."""

    #: Environment variable holding the historical telemetry Sheet URL. Shared
    #: with apps/station-monitor so one setting serves the whole repository.
    historical_url_variable: str = "HISTORICAL_DATA_URL"
    #: Environment variable holding the live telemetry Sheet URL, as used by
    #: StationWatch.
    live_url_variable: str = "STATIONWATCH_SHEET_URL"
    #: Explicit local override, for reproducible development only. It is never
    #: consulted unless the caller sets it, and the resolved source always says
    #: which kind it is.
    local_csv_variable: str = "BWB_ENVIRONMENTAL_CSV"
    http_timeout_seconds: float = 30.0
    #: Refuse to buffer a response larger than this. A misconfigured URL that
    #: points at something enormous should fail fast, not exhaust memory.
    max_response_bytes: int = 64 * 1024 * 1024
    timezone: object = STATION_TIMEZONE
    #: Rows whose timestamp cannot be parsed are dropped and counted. A single
    #: unparseable row is a data defect, not a reason to refuse the dataset.
    drop_unparseable_timestamps: bool = True
    #: Policy for two rows carrying the same instant. "first" keeps the earlier
    #: row in file order, matching the reliability audit's dedup convention.
    duplicate_timestamp_policy: str = "first"


@dataclass(frozen=True)
class QualityConfig:
    """Rules that decide whether an observation may be interpreted at all."""

    #: Nominal transmit cadence. Reused from the reliability audit rather than
    #: re-derived, so "expected" means one thing repository-wide.
    nominal_cycle_minutes: float = NOMINAL_CYCLE_MINUTES
    #: Two consecutive rows further apart than this are separated by a
    #: telemetry gap: no signal continuity may be assumed across it.
    continuity_gap_minutes: float = 30.0
    #: A gap at least this long is a station-wide outage rather than a handful
    #: of dropped packets. Matches the audit's GAP_MAJOR_MINUTES rationale.
    outage_gap_minutes: float = 480.0
    #: Rows arriving closer together than this are repeat transmissions from a
    #: fast-cycling node, not extra information.
    min_inter_arrival_minutes: float = 1.0
    #: The engine never interpolates to create or extend an event. This is the
    #: only place interpolation is permitted at all: filling a single missing
    #: sample inside an otherwise dense window, for descriptive statistics that
    #: require an even grid. Set to 0 to forbid it entirely.
    max_interpolation_gap_minutes: float = 0.0
    #: A window with fewer valid observations than this fraction of its
    #: schedulable slots cannot support an interpretation on its own.
    usable_coverage_fraction: float = 0.60
    partial_coverage_fraction: float = 0.30
    #: Soil zeros are ambiguous: the ingestion script blanks zero values, so a
    #: stored zero predates that behaviour and may be a sentinel rather than a
    #: measurement. Zeros are excluded from soil analysis and counted.
    treat_soil_zero_as_invalid: bool = True


@dataclass(frozen=True)
class WetnessDetectorConfig:
    """The wetness-event detector.

    The signal is a raw ADC value that sits pinned at a hard *dry rail* and
    excursions are strictly one-sided and downward (see README, "Why not a
    robust z-score"). Thresholds are therefore absolute ADC counts measured
    against an adaptive dry reference, not standardised deviations.
    """

    #: Sign convention. ``-1`` means "lower ADC counts mean wetter", which is
    #: what this deployment's data shows. Configurable because a rewired or
    #: replaced sensor board could invert it, and because the polarity is an
    #: empirical finding rather than a documented hardware fact.
    wet_direction: int = -1
    #: Width of the window used to estimate the dry reference level.
    baseline_window: str = "7D"
    #: Quantile of that window taken as the dry reference. An upper quantile,
    #: not the median: the dry state is the ceiling, so a median reference is
    #: dragged down by multi-day wet spells while an upper quantile is not.
    baseline_quantile: float = 0.90
    #: Minimum valid samples inside the window before a reference is computed.
    baseline_min_samples: int = 20
    #: Fallback reference when the rolling window cannot be estimated, taken as
    #: this quantile of the whole available record.
    global_baseline_quantile: float = 0.90
    #: Deviation, in ADC counts below the dry reference, needed to open an event.
    enter_counts: float = 30.0
    #: Deviation below which an open event may begin to close. Hysteresis: the
    #: exit threshold is deliberately lower than the entry threshold so a signal
    #: hovering near the boundary cannot flicker an event on and off.
    exit_counts: float = 10.0
    #: Consecutive qualifying samples needed to open an event. At the nominal
    #: 5-minute cadence three samples is ~10 minutes, which removes single-point
    #: spikes without discarding genuine short showers.
    enter_persistence_samples: int = 3
    #: Consecutive samples below ``exit_counts`` needed to close an event.
    exit_persistence_samples: int = 3
    #: Two events separated by less than this are one event with a dry spell in
    #: the middle, not two events.
    merge_gap_minutes: float = 60.0
    #: A detected interval below either floor is discarded.
    min_event_samples: int = 3
    min_event_duration_minutes: float = 10.0
    #: No event may span a telemetry gap longer than this. The station being
    #: switched off is not the end of a wetting event, and it is not evidence
    #: that one continued either, so the interval is cut and both sides are
    #: marked censored.
    max_internal_gap_minutes: float = 30.0
    #: --- classification ---
    #: Peak deviation and duration a candidate must reach to be called probable.
    probable_min_peak_counts: float = 100.0
    probable_min_duration_minutes: float = 20.0
    #: Relative humidity that corroborates surface wetting. Chosen from this
    #: dataset: rows at the dry rail have a median RH of 77% and a first
    #: quartile of 57%, while rows even 1-10 counts below it have a median RH
    #: of 96%. 90% sits comfortably inside the wet population.
    corroborating_humidity_pct: float = 90.0
    #: Fraction of an event's samples that must meet the humidity criterion.
    corroborating_humidity_fraction: float = 0.5
    #: An event with less than this fraction of its expected samples present is
    #: reported as uncertain regardless of its magnitude.
    min_event_coverage: float = 0.60


@dataclass(frozen=True)
class SoilResponseConfig:
    """When a soil-signal change may be called a response, and when it may not."""

    #: Analysis windows around the event.
    pre_event_window_hours: float = 24.0
    post_event_window_hours: float = 48.0
    #: Minimum valid soil observations required in each window before any
    #: verdict other than UNKNOWN is permitted.
    min_baseline_samples: int = 6
    min_response_samples: int = 6
    #: The pre-event window must also *span* enough time; six samples clustered
    #: into ten minutes do not characterise a baseline.
    min_baseline_span_minutes: float = 120.0
    #: Fraction of window observations that may be ambiguous zeros before the
    #: window is declared unusable.
    max_zero_fraction: float = 0.25
    #: Response criterion. A deviation must exceed BOTH a noise-relative and an
    #: absolute floor, and must persist.
    robust_sigma_multiple: float = 3.0
    #: Absolute floor in ADC counts. Set above the diurnal swing this sensor
    #: shows in its densest month (~165 counts peak-to-trough in July 2026), so
    #: an ordinary daily cycle cannot masquerade as a wetting response.
    min_absolute_counts: float = 180.0
    #: Floor on the estimated noise scale. The dry-state MAD of these ADC
    #: channels is frequently exactly zero, which would make any deviation
    #: infinitely significant.
    min_sigma_counts: float = 10.0
    #: Consecutive qualifying samples required. Four samples is ~20 minutes at
    #: the nominal cadence; a single excursion is not a response.
    min_persistence_samples: int = 4
    #: How long after the wetting *ends* a deviation may still be attributed to
    #: it. Without this, the 48-hour post-event window lets a soil change two
    #: days later -- with a dry night and another weather system in between --
    #: be reported as a response to a ten-minute shower. Manual review of the
    #: historical record found exactly that: five of eleven detections had
    #: onsets between 13 and 46 hours after a short event. Measured from the
    #: end rather than the start because water keeps arriving while the surface
    #: is wet, so a long event may legitimately produce a late onset. Twelve
    #: hours is a judgement call, not a measured infiltration time, and it is
    #: configurable for that reason.
    max_response_delay_hours: float = 12.0
    #: Remove the sensor's time-of-day cycle before testing, when it can be
    #: estimated. See README, "The soil signal has a diurnal confound".
    apply_diurnal_adjustment: bool = True
    diurnal_profile_window_days: float = 7.0
    diurnal_min_samples_per_hour: int = 3


@dataclass(frozen=True)
class PostEventDynamicsConfig:
    """Descriptive post-event trajectory analysis and optional model fitting."""

    window_hours: float = 72.0
    min_samples: int = 12
    #: Model fitting is opt-in. Descriptive measures are always computed.
    fit_models: bool = False
    min_fit_samples: int = 20
    #: Candidate relaxation rates searched, in 1/hour, when fitting
    #: ``M(t) = M_inf + A * exp(-k t)``. A deterministic grid is used instead of
    #: an iterative optimiser so the engine needs no SciPy and so repeated runs
    #: give bit-identical parameters.
    exponential_rate_grid_min: float = 0.005
    exponential_rate_grid_max: float = 5.0
    exponential_rate_grid_points: int = 240


@dataclass(frozen=True)
class BaselineConfig:
    """Environmental baseline (climatology) characterisation."""

    #: Calendar grouping for seasonal baselines.
    period: str = "M"
    #: A period whose telemetry coverage falls below this is reported with its
    #: coverage attached and marked as weak evidence; it is never silently
    #: averaged in with a complete month.
    strong_coverage_fraction: float = 0.75
    usable_coverage_fraction: float = 0.40
    #: Minimum valid observations before a period statistic is emitted at all.
    min_period_samples: int = 100
    #: Minimum observations in an hour-of-day bucket before a diurnal point is
    #: emitted.
    min_diurnal_samples: int = 20


@dataclass(frozen=True)
class AnomalyConfig:
    """Conditional-baseline anomaly detection."""

    #: Anomalies are judged against observations from a comparable season and
    #: time of day, not against the whole record.
    day_of_year_window: int = 15
    hour_window: int = 1
    #: Minimum comparable historical observations before any verdict is given.
    min_history_samples: int = 30
    #: Robust z threshold, computed as (x - median) / (1.4826 * MAD).
    robust_z_threshold: float = 3.5
    #: A value must also fall outside this percentile envelope. Requiring both
    #: keeps a tight-but-noisy distribution from producing constant anomalies.
    low_percentile: float = 1.0
    high_percentile: float = 99.0
    #: Floors on the estimated scale, per canonical sensor, so a quantised or
    #: flat historical window cannot yield an infinite z.
    min_sigma: object = field(
        default_factory=lambda: {
            sensors.TEMPERATURE: 0.5,
            sensors.HUMIDITY: 2.0,
            sensors.PRESSURE: 1.0,
            sensors.SOIL_SIGNAL: 25.0,
            sensors.WETNESS_SIGNAL: 25.0,
        }
    )
    #: Rate-of-change anomalies use the same machinery on first differences.
    rate_min_history_samples: int = 200
    rate_robust_z_threshold: float = 6.0
    #: A rate must also land in the extreme tail of all observed rates. The
    #: 99.9th percentile is the same statistical tail the sensor-health project
    #: uses for its rate outliers, kept identical so the two analyses agree on
    #: what "extreme" means. Without this second gate the tightly clustered
    #: temperature-rate distribution flags ordinary morning warming: the robust
    #: z alone produced 277 findings on the historical record, the pair
    #: produces a handful.
    rate_extreme_percentile: float = 99.9
    #: Floors on the estimated rate scale, per canonical sensor, in units per
    #: minute. Set to the level floor above divided by one nominal 5-minute
    #: transmit cycle, so a rate is only "extreme" if it moves the sensor by
    #: more than its resolution floor within a cycle. Without this the tightly
    #: clustered rate distribution yields robust z-scores in the millions.
    rate_min_sigma: object = field(
        default_factory=lambda: {
            sensors.TEMPERATURE: 0.1,
            sensors.HUMIDITY: 0.4,
            sensors.PRESSURE: 0.2,
        }
    )
    #: An observation is never compared against the rest of its own day. A
    #: multi-hour excursion sitting inside its own comparison window raises its
    #: own percentile envelope and partially masks itself; excluding the day
    #: removes that for episodes shorter than a day. Episodes lasting several
    #: days still contaminate their own baseline -- a documented limitation.
    exclude_same_day: bool = True
    #: Flagged samples closer together than this are one anomaly. An extreme
    #: excursion contaminates its own comparison window, which can punch holes
    #: in an otherwise continuous run; merging closes them.
    merge_gap_minutes: float = 60.0
    #: A wetness event lasting longer than this percentile of all observed
    #: events is reported as unusually persistent.
    persistence_percentile: float = 95.0


@dataclass(frozen=True)
class CurrentStateConfig:
    """Freshness rules for the "what is it doing right now" view."""

    fresh_max_minutes: float = 15.0
    stale_max_minutes: float = 180.0
    #: Window summarised as "recent conditions".
    recent_window_hours: float = 24.0
    #: Recent events surfaced with the current state.
    recent_event_window_hours: float = 72.0


@dataclass(frozen=True)
class EnvironmentalConfig:
    """The whole configuration for one analysis run."""

    ingestion: IngestionConfig = field(default_factory=IngestionConfig)
    quality: QualityConfig = field(default_factory=QualityConfig)
    wetness: WetnessDetectorConfig = field(default_factory=WetnessDetectorConfig)
    soil: SoilResponseConfig = field(default_factory=SoilResponseConfig)
    dynamics: PostEventDynamicsConfig = field(default_factory=PostEventDynamicsConfig)
    baseline: BaselineConfig = field(default_factory=BaselineConfig)
    anomaly: AnomalyConfig = field(default_factory=AnomalyConfig)
    current_state: CurrentStateConfig = field(default_factory=CurrentStateConfig)
    #: Station identifier. Event ids are namespaced by it so a second station
    #: can be added later without colliding with historical ids.
    station_id: str = "bwb-cpa-01"

    def with_overrides(self, **sections):
        """Return a copy with whole configuration sections replaced."""
        return replace(self, **sections)


DEFAULT_CONFIG = EnvironmentalConfig()
