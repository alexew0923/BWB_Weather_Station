"""Thin orchestration adapter around the battery analysis engine."""

import contextlib
import io
import os
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

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
LOCAL_HISTORICAL_CSV_VARIABLE = "BWB_HISTORICAL_CSV"
HISTORICAL_DATA_URL_VARIABLE = "HISTORICAL_DATA_URL"
LOCAL_HISTORICAL_SOURCE_LABEL = "Local historical data"
REMOTE_HISTORICAL_SOURCE_LABEL = "Remote historical data"


class HistoricalDataError(Exception):
    """The app could not retrieve a usable historical CSV source."""

    def __init__(self, summary, detail):
        super().__init__(detail)
        self.summary = summary
        self.detail = detail


def resolve_historical_source(environ=None):
    """Select the shared historical source without changing local-first precedence."""
    environment = os.environ if environ is None else environ
    local_setting = (environment.get(LOCAL_HISTORICAL_CSV_VARIABLE) or "").strip()
    remote_setting = (environment.get(HISTORICAL_DATA_URL_VARIABLE) or "").strip()
    if local_setting:
        return {
            "kind": "local",
            "local_path": Path(local_setting).expanduser(),
            "remote_url": None,
            "display_label": LOCAL_HISTORICAL_SOURCE_LABEL,
        }
    if remote_setting:
        return {
            "kind": "remote",
            "local_path": None,
            "remote_url": remote_setting,
            "display_label": REMOTE_HISTORICAL_SOURCE_LABEL,
        }
    return {
        "kind": "missing",
        "local_path": None,
        "remote_url": None,
        "display_label": None,
    }


def safe_historical_error_detail(error, source):
    """Keep local filesystem paths out of user-visible error details."""
    detail = str(error)
    path = source.get("local_path")
    if source.get("kind") != "local" or path is None:
        return detail

    candidates = {str(path), str(path.absolute()), str(path.resolve())}
    for candidate in sorted(candidates, key=len, reverse=True):
        if candidate:
            detail = detail.replace(candidate, "local historical CSV")
    return detail


def normalize_historical_data_url(url):
    """Validate a URL and convert Google Sheets edit links to CSV exports."""
    value = (url or "").strip()
    try:
        parsed = urlparse(value)
    except ValueError as error:
        raise HistoricalDataError(
            "The historical telemetry URL is malformed.", str(error)
        ) from error
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HistoricalDataError(
            "The historical telemetry URL is malformed.",
            f"{HISTORICAL_DATA_URL_VARIABLE} must be an HTTP or HTTPS URL.",
        )

    marker = "/spreadsheets/d/"
    if parsed.netloc == "docs.google.com" and marker in parsed.path:
        sheet_id = parsed.path.split(marker, 1)[1].split("/", 1)[0]
        query = parse_qs(parsed.query)
        fragment = parse_qs(parsed.fragment)
        gid = (query.get("gid") or fragment.get("gid") or ["0"])[0]
        if not sheet_id or not gid.isdigit():
            raise HistoricalDataError(
                "The Google Sheets historical URL is incomplete.",
                "A spreadsheet ID and numeric gid are required.",
            )
        return (
            f"https://docs.google.com/spreadsheets/d/{sheet_id}/"
            f"export?format=csv&gid={gid}"
        )
    return value


def fetch_historical_csv(url, timeout=30):
    """Fetch UTF-8 CSV text for the app without involving the analysis engine."""
    export_url = normalize_historical_data_url(url)
    try:
        with urlopen(export_url, timeout=timeout) as response:
            text = response.read().decode("utf-8-sig")
    except ValueError as error:
        raise HistoricalDataError(
            "The historical telemetry URL is malformed.", str(error)
        ) from error
    except (HTTPError, URLError, TimeoutError, OSError, UnicodeError) as error:
        raise HistoricalDataError(
            "The historical telemetry Sheet could not be retrieved.", str(error)
        ) from error

    stripped = text.lstrip()
    if not stripped:
        raise HistoricalDataError(
            "The historical telemetry Sheet returned no data.",
            "The CSV response was empty.",
        )
    if stripped[:100].lower().startswith(("<!doctype html", "<html")):
        raise HistoricalDataError(
            "The historical telemetry URL did not return CSV data.",
            "Use a published or publicly readable Google Sheets CSV export.",
        )
    return text


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
        *reliability_fingerprint(reliability_output),
    )


def reliability_fingerprint(reliability_output):
    """Fingerprint optional local context without tying remote data to a file."""
    return (
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


def load_battery_analysis_from_csv_text(csv_text, reliability_output):
    """Bridge fetched CSV text to the unchanged path-based analysis engine."""
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".csv", delete=False
        ) as handle:
            handle.write(csv_text)
            temporary_path = Path(handle.name)
        return load_battery_analysis(str(temporary_path), reliability_output)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
