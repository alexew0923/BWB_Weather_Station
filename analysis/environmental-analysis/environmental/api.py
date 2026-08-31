"""Stage 11: the public API.

This is the surface a CLI, a notebook, a Flask view or a FastAPI route is meant
to use. It returns typed domain objects that serialise cleanly to JSON; no
DataFrame ever crosses this boundary.

Caching is deliberately absent. See the README, "Caching": the engine fetches
the remote Sheet exactly once per analysis run and then works from the
canonical dataset, so there is nothing for an in-process cache to save within a
run. Caching *between* runs is a deployment concern -- a Streamlit app has
``st.cache_data``, a web service has its own layer -- and putting a TTL cache
in here would add shared mutable state to a library whose main promise is
determinism. Callers cache ``load_environmental_dataset`` or
``fetch_csv_text``; the engine stays pure.
"""

from datetime import datetime, timezone

from . import sensors
from .anomalies import detect_anomalies
from .baselines import build_environmental_baseline, wetness_deviation
from .characterization import characterize_event
from .config import EnvironmentalConfig
from .current_state import (
    get_recent_environmental_state,
    load_current_environmental_state,
)
from .dataset import EnvironmentalDataset, load_environmental_dataset
from .dynamics import analyze_post_event_dynamics
from .errors import UnknownEventError
from .events import detect_wetness_intervals, detector_metadata
from .models import (
    EnvironmentalSummary,
    EventClassification,
    SoilResponseStatus,
)
from .profiling import profile_environment, telemetry_profile
from .quality import dataset_quality
from .soil import analyze_soil_response, build_soil_context
from .version import ENGINE_VERSION, version_metadata

LIMITATIONS = (
    "The wetness channel is an uncalibrated ADC signal. No rainfall depth, "
    "rate or accumulation can be derived from it.",
    "The soil probe is uncalibrated and its polarity is not established by "
    "this repository or by this record, so soil changes are reported in raw "
    "counts and are never called wetter or drier.",
    "Timestamps are Apps Script receipt times, not sensor read times.",
    "Telemetry coverage over the record is far below 100%; missing telemetry "
    "is never interpreted as environmental behaviour.",
    "The station is powered down overnight from 2026-04-21, so events that "
    "begin or end in the dark hours are observed only in part.",
    "The record covers a single partial year, so seasonal baselines and "
    "anomalies are relative to this deployment, not to a regional climatology.",
    "No causal attribution is made: this station cannot tell rain from dew, "
    "fog, snowmelt or irrigation.",
)


def detect_environmental_events(dataset, config=None, include_dynamics=None):
    """Detect and fully characterise every wetting event in a dataset.

    Deterministic: the same dataset and configuration always produce the same
    events, in the same order, with the same identifiers.
    """
    settings = config or dataset.config
    frame = wetness_deviation(dataset, settings.wetness)
    intervals = detect_wetness_intervals(dataset, settings.wetness, frame)
    soil_context = build_soil_context(dataset, settings.soil)

    want_dynamics = (
        settings.dynamics.fit_models if include_dynamics is None else include_dynamics
    )

    events = []
    for interval in intervals:
        soil = analyze_soil_response(dataset, interval, soil_context, settings.soil)
        dynamics = None
        if soil.status is SoilResponseStatus.DETECTED or want_dynamics:
            dynamics = analyze_post_event_dynamics(
                dataset, interval, soil, soil_context, settings.dynamics
            )
        events.append(
            characterize_event(dataset, interval, frame, soil, dynamics, settings)
        )
    events.sort(key=lambda event: (event.start_time, event.event_id))
    return tuple(events)


def list_events(events, classification=None, soil_status=None, start=None, end=None,
                limit=None):
    """Filter a detected event collection. Ordering is always chronological."""
    selected = list(events)
    if classification is not None:
        wanted = (
            classification
            if isinstance(classification, (list, tuple, set))
            else [classification]
        )
        wanted = {EventClassification(str(item)) for item in wanted}
        selected = [e for e in selected if e.classification in wanted]
    if soil_status is not None:
        wanted = (
            soil_status
            if isinstance(soil_status, (list, tuple, set))
            else [soil_status]
        )
        wanted = {SoilResponseStatus(str(item)) for item in wanted}
        selected = [e for e in selected if e.soil_response.status in wanted]
    if start is not None:
        selected = [e for e in selected if e.end_time >= start]
    if end is not None:
        selected = [e for e in selected if e.start_time <= end]
    if limit is not None:
        selected = selected[:limit]
    return tuple(selected)


def get_event(events, event_id):
    """Return one event by id, or raise a domain error naming the id."""
    for event in events:
        if event.event_id == event_id:
            return event
    raise UnknownEventError(
        f"no event with id {event_id!r} exists in this analysis",
        summary="That environmental event does not exist in this analysis.",
    )


def get_environmental_baseline(dataset, events=(), config=None):
    """Characterise the station's environmental normals."""
    return build_environmental_baseline(dataset, events, config or dataset.config)


def analyze_environment(dataset, config=None, include_anomalies=True):
    """Run the whole pipeline over a dataset and return one summary object."""
    settings = config or dataset.config
    events = detect_environmental_events(dataset, settings)
    baseline = get_environmental_baseline(dataset, events, settings)
    anomalies = (
        tuple(detect_anomalies(dataset, events, settings.anomaly))
        if include_anomalies
        else ()
    )

    classification_counts = {
        item.value: sum(1 for e in events if e.classification is item)
        for item in EventClassification
    }
    soil_counts = {
        item.value: sum(1 for e in events if e.soil_response.status is item)
        for item in SoilResponseStatus
    }

    return EnvironmentalSummary(
        station_id=settings.station_id,
        generated_at=datetime.now(timezone.utc),
        source={
            **(dataset.source.to_dict() if dataset.source else {}),
            "detector": detector_metadata(settings.wetness),
        },
        coverage=telemetry_profile(dataset),
        profiles=profile_environment(dataset),
        events=events,
        event_counts={"total": len(events), **classification_counts},
        soil_response_counts=soil_counts,
        baseline=baseline,
        anomalies=anomalies,
        quality=dataset_quality(dataset),
        limitations=LIMITATIONS,
        versions=version_metadata(),
    )


def get_environmental_summary(config=None, opener=None, environ=None,
                              csv_text=None, allow_local_override=True):
    """Load the production dataset and analyse it in one call."""
    settings = config or EnvironmentalConfig()
    dataset = load_environmental_dataset(
        config=settings,
        opener=opener,
        csv_text=csv_text,
        environ=environ,
        allow_local_override=allow_local_override,
    )
    return analyze_environment(dataset, settings)


def get_current_environmental_state(config=None, opener=None, environ=None,
                                    now=None, csv_text=None):
    """The newest defensible statement about conditions, or why there is none."""
    return load_current_environmental_state(
        config=config, opener=opener, environ=environ, now=now, csv_text=csv_text
    )


__all__ = [
    "EnvironmentalConfig",
    "EnvironmentalDataset",
    "LIMITATIONS",
    "analyze_environment",
    "detect_environmental_events",
    "get_current_environmental_state",
    "get_environmental_baseline",
    "get_environmental_summary",
    "get_event",
    "get_recent_environmental_state",
    "list_events",
    "load_environmental_dataset",
    "profile_environment",
    "sensors",
    "telemetry_profile",
    "version_metadata",
    "ENGINE_VERSION",
]
