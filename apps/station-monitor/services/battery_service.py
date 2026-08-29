"""Thin orchestration adapter around the battery analysis engine."""

import contextlib
import io
from pathlib import Path

from services import analysis_imports as _analysis_imports  # noqa: F401
from battery_analysis import (  # noqa: E402
    DEFAULT_RELIABILITY_PROJECT,
    MIN_TREND_COVERAGE,
    MIN_TREND_SAMPLES,
    STATION_TIMEZONE,
    add_slot_index,
    analyze_relationships,
    build_battery_summary,
    compute_daily_battery_metrics,
    compute_gaps,
    compute_outage_battery_context,
    compute_rolling_battery_metrics,
    detect_outages,
    load_and_validate_data,
    load_reliability_exports,
    reliability_exports_match_data,
    significant_outages,
)
from energy_model import EnergyModelParameters, model_daily_power_budget  # noqa: E402


DEFAULT_DATA_PATH = DEFAULT_RELIABILITY_PROJECT / "data" / "HistoricalData.csv"
DEFAULT_RELIABILITY_OUTPUT = DEFAULT_RELIABILITY_PROJECT / "audit_output"


def file_fingerprint(path):
    """Return stable file metadata for presentation-layer cache invalidation."""
    path = Path(path).resolve()
    if not path.exists() or not path.is_file():
        return str(path), None, None
    stat = path.stat()
    return str(path), stat.st_mtime_ns, stat.st_size


def analysis_fingerprint(csv_path, reliability_output):
    """Fingerprint the historical source and optional reliability exports."""
    return (
        file_fingerprint(csv_path),
        file_fingerprint(Path(reliability_output) / "outage_intervals.csv"),
        file_fingerprint(Path(reliability_output) / "daily_reliability.csv"),
    )


def load_battery_analysis(csv_path, reliability_output):
    """Compute dashboard data exclusively through existing engine functions."""
    validation_output = io.StringIO()
    with contextlib.redirect_stdout(validation_output):
        frame, _ = load_and_validate_data(csv_path)

    outages, reliability_daily = load_reliability_exports(reliability_output)
    reliability_source = "exported reliability-audit CSVs"
    if outages is None or not reliability_exports_match_data(frame, reliability_daily):
        indexed = add_slot_index(frame)
        outages = detect_outages(indexed, compute_gaps(indexed))
        reliability_daily = None
        reliability_source = "stable reliability helpers (exports unavailable or stale)"

    daily = compute_daily_battery_metrics(
        frame, outages=outages, reliability_daily=reliability_daily
    )
    rolling = compute_rolling_battery_metrics(frame)
    outage_context = compute_outage_battery_context(frame, outages)
    relationships = analyze_relationships(daily)
    summary = build_battery_summary(frame, daily, outage_context, relationships)
    summary["reliability_context_source"] = reliability_source
    return {
        "daily": daily,
        "rolling": rolling,
        "outages": significant_outages(outages).reset_index(drop=True),
        "outage_context": outage_context,
        "relationships": relationships,
        "summary": summary,
        "validation_log": validation_output.getvalue(),
    }
