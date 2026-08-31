"""Better With Bees environmental analysis engine.

A framework-independent domain layer that turns the station's telemetry into
structured, uncertainty-aware environmental intelligence.

    from environmental import load_environmental_dataset, analyze_environment

    dataset = load_environmental_dataset()
    summary = analyze_environment(dataset)
    for event in summary.events:
        print(event.event_id, event.classification, event.soil_response.status)

Nothing in this package imports a web framework, and nothing in it prints. It
is usable unchanged from a CLI, a test, a notebook, Flask, FastAPI, Streamlit
or a future service.
"""

from .api import (
    LIMITATIONS,
    analyze_environment,
    detect_environmental_events,
    get_current_environmental_state,
    get_environmental_baseline,
    get_environmental_summary,
    get_event,
    get_recent_environmental_state,
    list_events,
    load_environmental_dataset,
    profile_environment,
    telemetry_profile,
)
from .config import (
    AnomalyConfig,
    BaselineConfig,
    CurrentStateConfig,
    EnvironmentalConfig,
    IngestionConfig,
    PostEventDynamicsConfig,
    QualityConfig,
    SoilResponseConfig,
    WetnessDetectorConfig,
)
from .dataset import EnvironmentalDataset, build_dataset_from_csv_text
from .errors import (
    ConfigurationError,
    EmptyDatasetError,
    EnvironmentalAnalysisError,
    InsufficientDataError,
    SchemaError,
    SourceFormatError,
    SourceUnavailableError,
    UnknownEventError,
)
from .models import (
    CurrentEnvironmentalState,
    DataQuality,
    EnvironmentalAnomaly,
    EnvironmentalBaseline,
    EnvironmentalEvent,
    EnvironmentalSummary,
    EventClassification,
    EventType,
    EvidenceStrength,
    FreshnessState,
    SignalDirection,
    SoilResponse,
    SoilResponseStatus,
    StatementKind,
    to_serialisable,
)
from .version import ENGINE_NAME, ENGINE_VERSION, version_metadata

__all__ = [
    "AnomalyConfig",
    "BaselineConfig",
    "ConfigurationError",
    "CurrentEnvironmentalState",
    "CurrentStateConfig",
    "DataQuality",
    "ENGINE_NAME",
    "ENGINE_VERSION",
    "EmptyDatasetError",
    "EnvironmentalAnalysisError",
    "EnvironmentalAnomaly",
    "EnvironmentalBaseline",
    "EnvironmentalConfig",
    "EnvironmentalDataset",
    "EnvironmentalEvent",
    "EnvironmentalSummary",
    "EventClassification",
    "EventType",
    "EvidenceStrength",
    "FreshnessState",
    "IngestionConfig",
    "InsufficientDataError",
    "LIMITATIONS",
    "PostEventDynamicsConfig",
    "QualityConfig",
    "SchemaError",
    "SignalDirection",
    "SoilResponse",
    "SoilResponseConfig",
    "SoilResponseStatus",
    "SourceFormatError",
    "SourceUnavailableError",
    "StatementKind",
    "UnknownEventError",
    "WetnessDetectorConfig",
    "analyze_environment",
    "build_dataset_from_csv_text",
    "detect_environmental_events",
    "get_current_environmental_state",
    "get_environmental_baseline",
    "get_environmental_summary",
    "get_event",
    "get_recent_environmental_state",
    "list_events",
    "load_environmental_dataset",
    "profile_environment",
    "telemetry_profile",
    "to_serialisable",
    "version_metadata",
]
