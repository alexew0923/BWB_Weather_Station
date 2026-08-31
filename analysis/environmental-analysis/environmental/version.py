"""Version identifiers stamped onto every analytical result.

Analytical conclusions change when algorithms change. Every event, soil-response
verdict, baseline and anomaly produced by this engine carries the version of the
component that produced it, so a stored result can always be traced back to the
code that generated it.

Bump the component version whenever a change can alter the output for unchanged
input data. Bump ``ENGINE_VERSION`` for any release of the subsystem.
"""

ENGINE_NAME = "bwb-environmental-analysis"
ENGINE_VERSION = "1.0.0"

# Component versions. These are deliberately independent: improving the soil
# response criterion should not invalidate the provenance of stored wetness
# events.
WETNESS_DETECTOR_VERSION = "1.0.0"
SOIL_RESPONSE_VERSION = "1.0.0"
POST_EVENT_DYNAMICS_VERSION = "1.0.0"
BASELINE_VERSION = "1.0.0"
ANOMALY_DETECTOR_VERSION = "1.0.0"
DATASET_SCHEMA_VERSION = "1.0.0"


def version_metadata():
    """Return every version identifier as a plain, serialisable mapping."""
    return {
        "engine_name": ENGINE_NAME,
        "engine_version": ENGINE_VERSION,
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "wetness_detector_version": WETNESS_DETECTOR_VERSION,
        "soil_response_version": SOIL_RESPONSE_VERSION,
        "post_event_dynamics_version": POST_EVENT_DYNAMICS_VERSION,
        "baseline_version": BASELINE_VERSION,
        "anomaly_detector_version": ANOMALY_DETECTOR_VERSION,
    }
