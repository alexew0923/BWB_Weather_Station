"""Current and recent environmental state.

The live Sheet may legitimately be empty: the station is powered down
overnight, the tab is archived nightly, and there are long stretches with no
telemetry at all. An engine that fills that silence with the last value it saw
would be reporting last month's weather as today's.

So this module returns an explicit state instead:

``current``              fresh telemetry, conditions are known
``stale``                telemetry exists but is old; values are labelled as of
                         their observation time and not as "now"
``awaiting_telemetry``   the source was read and holds no observations
``insufficient_data``    observations exist but too few to characterise
``unavailable``          the source could not be read at all

``unavailable`` is an observation failure, never an environmental statement --
the same separation StationWatch draws between MONITOR ERROR and OFFLINE.
"""

import pandas as pd

from . import sensors
from .config import EnvironmentalConfig
from .dataset import load_environmental_dataset
from .errors import (
    ConfigurationError,
    EmptyDatasetError,
    EnvironmentalAnalysisError,
    SchemaError,
    SourceFormatError,
    SourceUnavailableError,
)
from .data_sources import resolve_live_source
from .models import (
    CurrentEnvironmentalState,
    DataQuality,
    FreshnessState,
    QualityAssessment,
    SensorReading,
)
from .quality import dataset_quality
from .statistics import describe
from .version import ENGINE_VERSION

MIN_STATE_OBSERVATIONS = 1


def _unavailable(config, error, source_label):
    return CurrentEnvironmentalState(
        station_id=config.station_id,
        freshness=FreshnessState.UNAVAILABLE,
        source_label=source_label,
        quality=QualityAssessment(
            level=DataQuality.INVALID, reasons=(error.detail,)
        ),
        summary=error.summary,
        engine_version=ENGINE_VERSION,
    )


def get_recent_environmental_state(dataset, now=None, events=(), anomalies=(),
                                   config=None, source_label=None):
    """Summarise the newest defensible conditions from a dataset."""
    settings = config or dataset.config
    state_config = settings.current_state
    label = source_label or (
        dataset.source.describe() if dataset.source else "unknown source"
    )

    if len(dataset) < MIN_STATE_OBSERVATIONS:
        return CurrentEnvironmentalState(
            station_id=settings.station_id,
            freshness=FreshnessState.AWAITING_TELEMETRY,
            as_of=pd.Timestamp(now) if now is not None else None,
            source_label=label,
            quality=QualityAssessment(
                level=DataQuality.INSUFFICIENT,
                reasons=("the source holds no observations",),
            ),
            summary="No telemetry has been received, so conditions are unknown.",
            engine_version=ENGINE_VERSION,
        )

    as_of = pd.Timestamp(now) if now is not None else pd.Timestamp.now(
        tz=dataset.timestamps.tz
    )
    if as_of.tzinfo is None:
        as_of = as_of.tz_localize(dataset.timestamps.tz)
    latest = dataset.end_time
    age_minutes = (as_of - latest).total_seconds() / 60.0

    if age_minutes <= state_config.fresh_max_minutes:
        freshness = FreshnessState.CURRENT
    elif age_minutes <= state_config.stale_max_minutes:
        freshness = FreshnessState.STALE
    else:
        freshness = FreshnessState.STALE

    window_start = latest - pd.Timedelta(hours=state_config.recent_window_hours)
    recent = dataset.subset(window_start, latest)

    readings = {}
    for sensor in dataset.sensor_columns:
        series = dataset.series(sensor).dropna()
        if series.empty:
            readings[sensor] = SensorReading(
                sensor=sensor,
                unit=sensors.SEMANTICS[sensor].unit,
                value=None,
                observed_at=None,
                calibrated=sensors.SEMANTICS[sensor].calibrated,
                quality=QualityAssessment(
                    level=DataQuality.INSUFFICIENT,
                    reasons=("no valid readings for this sensor",),
                ),
            )
            continue
        readings[sensor] = SensorReading(
            sensor=sensor,
            unit=sensors.SEMANTICS[sensor].unit,
            value=float(series.iloc[-1]),
            observed_at=series.index[-1],
            calibrated=sensors.SEMANTICS[sensor].calibrated,
            recent=describe(recent.series(sensor).dropna()),
            quality=QualityAssessment(
                level=DataQuality.USABLE,
                observations=len(recent),
                valid_observations=int(recent.series(sensor).notna().sum()),
            ),
        )

    if len(dataset) < 3:
        freshness = FreshnessState.INSUFFICIENT_DATA

    recent_events = tuple(
        event
        for event in events
        if event.end_time
        >= latest - pd.Timedelta(hours=state_config.recent_event_window_hours)
    )
    active = _active_wetness(dataset, events, latest)

    return CurrentEnvironmentalState(
        station_id=settings.station_id,
        freshness=freshness,
        as_of=as_of,
        latest_observation_at=latest,
        data_age_minutes=round(age_minutes, 2),
        source_label=label,
        readings=readings,
        active_wetness=active,
        recent_events=recent_events,
        anomalies=tuple(anomalies),
        quality=dataset_quality(dataset),
        summary=_summary(freshness, age_minutes, active),
        engine_version=ENGINE_VERSION,
    )


def _active_wetness(dataset, events, latest):
    ongoing = [
        event
        for event in events
        if event.start_time <= latest <= event.end_time
        or (event.end_time == latest and event.boundaries.end_censored)
    ]
    if not ongoing:
        return {
            "in_progress": False,
            "note": (
                "no wetting event was in progress at the newest observation"
            ),
        }
    event = ongoing[-1]
    return {
        "in_progress": True,
        "event_id": event.event_id,
        "started_at": event.start_time.isoformat(),
        "duration_minutes": event.duration_minutes,
        "classification": str(event.classification),
    }


def _summary(freshness, age_minutes, active):
    if freshness is FreshnessState.CURRENT:
        base = "Conditions are current."
    elif freshness is FreshnessState.STALE:
        base = (
            f"The newest observation is {age_minutes / 60:.1f} hours old; these "
            "are the last known conditions, not current ones."
        )
    elif freshness is FreshnessState.INSUFFICIENT_DATA:
        base = "Too few observations are available to characterise conditions."
    else:
        base = "Conditions are unknown."
    if active.get("in_progress"):
        base += " A wetting event was in progress at the newest observation."
    return base


def load_current_environmental_state(config=None, opener=None, environ=None,
                                     now=None, csv_text=None):
    """Load the live source and summarise it, degrading to an explicit state.

    Retrieval failures are converted into states rather than exceptions,
    because "I cannot see the station" is a legitimate answer to "what is it
    doing right now" and a frontend needs to render it.
    """
    settings = config or EnvironmentalConfig()
    label = "Remote live telemetry"
    try:
        source = None
        if csv_text is None:
            source = resolve_live_source(settings.ingestion, environ=environ)
            label = source.describe()
        dataset = load_environmental_dataset(
            config=settings,
            source=source,
            opener=opener,
            csv_text=csv_text,
            environ=environ,
            allow_local_override=False,
        )
    except EmptyDatasetError as error:
        return CurrentEnvironmentalState(
            station_id=settings.station_id,
            freshness=FreshnessState.AWAITING_TELEMETRY,
            source_label=label,
            quality=QualityAssessment(
                level=DataQuality.INSUFFICIENT, reasons=(error.detail,)
            ),
            summary=(
                "The live telemetry source was read successfully and holds no "
                "observations, so current conditions are unknown."
            ),
            engine_version=ENGINE_VERSION,
        )
    except (
        ConfigurationError,
        SourceUnavailableError,
        SourceFormatError,
        SchemaError,
    ) as error:
        return _unavailable(settings, error, label)
    except EnvironmentalAnalysisError as error:  # pragma: no cover - defensive
        return _unavailable(settings, error, label)

    return get_recent_environmental_state(
        dataset, now=now, config=settings, source_label=label
    )
