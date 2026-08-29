"""Thin application adapter around the reliability incident engine."""

import contextlib
import io
import tempfile
from pathlib import Path

from services import analysis_imports as _analysis_imports  # noqa: F401
from services.battery_service import (  # noqa: E402
    HISTORICAL_DATA_URL_VARIABLE,
    HistoricalDataError,
    fetch_historical_csv,
    file_fingerprint,
    normalize_historical_data_url,
    resolve_historical_source,
    safe_historical_error_detail,
)
from data_validation import load_and_validate_data  # noqa: E402
from incident_analysis import analyze_incident  # noqa: E402
from incident_report import plot_incident  # noqa: E402
from outage_analysis import (  # noqa: E402
    compute_gaps,
    detect_outages,
    significant_outages,
)
from reliability_metrics import add_slot_index  # noqa: E402


DEFAULT_CONTEXT_HOURS = 12.0


def load_incident_catalog(csv_path):
    """Validate history and detect significant outages with the audit engine."""
    validation_output = io.StringIO()
    with contextlib.redirect_stdout(validation_output):
        frame, validation = load_and_validate_data(csv_path)

    indexed = add_slot_index(frame)
    detected = detect_outages(indexed, compute_gaps(indexed))
    incidents = significant_outages(detected).reset_index(drop=True).copy()
    incidents.insert(0, "incident_id", range(1, len(incidents) + 1))
    return {
        "frame": indexed,
        "incidents": incidents,
        "validation": validation,
        "validation_log": validation_output.getvalue(),
    }


def load_incident_catalog_from_csv_text(csv_text):
    """Bridge fetched CSV text to the unchanged path-based audit loader."""
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".csv", delete=False
        ) as handle:
            handle.write(csv_text)
            temporary_path = Path(handle.name)
        return load_incident_catalog(str(temporary_path))
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def analyze_selected_incident(frame, incident, source=None):
    """Run the existing evidence-bounded analysis for one detected outage."""
    metadata = dict(incident)
    return analyze_incident(
        frame,
        metadata["gap_start"],
        metadata["gap_end"],
        before_hours=DEFAULT_CONTEXT_HOURS,
        after_hours=DEFAULT_CONTEXT_HOURS,
        source=source,
        detected_outage=metadata,
    )


def render_incident_plot(frame, report):
    """Render the existing CLI timeline and return its PNG bytes."""
    with tempfile.TemporaryDirectory(prefix="bwb-incident-plot-") as directory:
        path = Path(directory) / "incident.png"
        plot_incident(frame, report, path)
        return path.read_bytes()
