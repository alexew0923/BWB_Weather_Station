"""Retrieval of telemetry CSV from the project's Google Sheets.

Production reads the Sheet over HTTP. There is no checked-in CSV, no bundled
fixture and no silent fallback to a stale local file: an engine that quietly
answers from month-old data when the network fails is worse than one that says
it cannot answer.

A local file may be used, but only when a caller asks for it explicitly, and
the resolved source always reports which kind it is so a UI can label it.

URL handling follows the convention already established in
``apps/station-monitor/services/battery_service.py``: a Google Sheets edit link
is normalised to its CSV export form, and anything else usable is passed
through untouched.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

from ..config import IngestionConfig
from ..errors import ConfigurationError, SourceFormatError, SourceUnavailableError

PROJECT_DIR = Path(__file__).resolve().parents[2]

# Environment files consulted, in order, for a setting that is not already in
# the real environment. The StationWatch file is included because that is where
# this repository already keeps the live telemetry URL; see
# analysis/stationwatch-live/README.md, which documents that the URL is
# deployment configuration rather than a secret.
ENV_FILES = (
    PROJECT_DIR / ".env",
    PROJECT_DIR.parent / "stationwatch-live" / ".env",
)

HTML_PREFIXES = ("<!doctype html", "<html", "<!DOCTYPE HTML")


@dataclass(frozen=True)
class SourceReference:
    """Where one dataset came from, in a form safe to show a user.

    ``url`` and ``path`` are kept for the engine's own use. ``describe`` never
    returns either of them: a dashboard should not print a filesystem path, and
    the deployment URL is a configuration detail rather than analysis.
    """

    kind: str            # "remote" | "local"
    label: str
    setting: str
    url: str | None = None
    path: Path | None = None

    def describe(self):
        """A short, safe description of the source for display."""
        return self.label

    def to_dict(self):
        return {"kind": self.kind, "label": self.label, "setting": self.setting}


def load_env_files(environ=None, files=ENV_FILES):
    """Populate missing settings from local ``.env`` files.

    Real environment variables always win, so an exported value is never
    overwritten by a file. Missing or unreadable files are ignored: ``.env`` is
    optional everywhere in this repository.
    """
    environment = os.environ if environ is None else environ
    for path in files:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            environment.setdefault(key.strip(), value.strip().strip("\"'"))
    return environment


def _resolve(config, variable, remote_label, environ, allow_local):
    # An explicitly supplied mapping is the caller's whole environment: local
    # .env files are consulted only for the real process environment, so a test
    # or a service that passes its own settings gets exactly those settings.
    if environ is None:
        environment = load_env_files(os.environ)
    else:
        environment = environ

    if allow_local:
        local = (environment.get(config.local_csv_variable) or "").strip()
        if local:
            return SourceReference(
                kind="local",
                label="Local development CSV",
                setting=config.local_csv_variable,
                path=Path(local).expanduser(),
            )

    remote = (environment.get(variable) or "").strip()
    if not remote:
        raise ConfigurationError(
            f"{variable} is not set; export it or add it to a local .env file",
            summary=(
                f"No telemetry source is configured ({variable})."
            ),
        )
    return SourceReference(
        kind="remote", label=remote_label, setting=variable, url=remote
    )


def resolve_historical_source(config=None, environ=None, allow_local_override=True):
    """Resolve the historical telemetry source.

    Remote-first. The local override exists for reproducible development and is
    only consulted when the caller has explicitly set the override variable, so
    a production deployment cannot drift onto a stale file by accident.
    """
    config = config or IngestionConfig()
    return _resolve(
        config,
        config.historical_url_variable,
        "Remote historical telemetry",
        environ,
        allow_local_override,
    )


def resolve_live_source(config=None, environ=None):
    """Resolve the live telemetry source, as configured for StationWatch."""
    config = config or IngestionConfig()
    return _resolve(
        config, config.live_url_variable, "Remote live telemetry", environ, False
    )


def normalize_sheet_url(url, setting="telemetry URL"):
    """Validate a URL and convert a Google Sheets link to its CSV export form.

    Accepted:
      * ``https://docs.google.com/spreadsheets/d/<id>/edit?gid=<gid>#gid=<gid>``
      * ``https://docs.google.com/spreadsheets/d/<id>/export?format=csv&gid=<gid>``
      * ``https://docs.google.com/spreadsheets/d/<id>/gviz/tq?tqx=out:csv&sheet=<name>``
      * any other HTTP(S) URL serving CSV

    The gviz form is passed through rather than rewritten: it selects a tab by
    name, which cannot be expressed as a gid without another network call.
    """
    value = (url or "").strip()
    try:
        parsed = urlparse(value)
    except ValueError as error:
        raise ConfigurationError(
            f"{setting} is malformed: {error}",
            summary="The configured telemetry URL is not a valid URL.",
        ) from error

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigurationError(
            f"{setting} must be an http or https URL, got {value!r}",
            summary="The configured telemetry URL is not a valid URL.",
        )

    marker = "/spreadsheets/d/"
    if parsed.netloc != "docs.google.com" or marker not in parsed.path:
        return value

    if "/gviz/" in parsed.path:
        return value

    sheet_id = parsed.path.split(marker, 1)[1].split("/", 1)[0]
    query = parse_qs(parsed.query)
    fragment = parse_qs(parsed.fragment)
    if "/export" in parsed.path and (query.get("format") or [""])[0] == "csv":
        return value

    gid = (query.get("gid") or fragment.get("gid") or ["0"])[0]
    if not sheet_id or not gid.isdigit():
        raise ConfigurationError(
            f"{setting} needs a spreadsheet id and a numeric gid: {value!r}",
            summary="The configured Google Sheets URL is incomplete.",
        )
    return (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/"
        f"export?format=csv&gid={gid}"
    )


def _validate_csv_text(text, source_description):
    """Reject responses that are not CSV before pandas ever sees them."""
    stripped = text.lstrip("﻿").lstrip()
    if not stripped:
        raise SourceFormatError(
            f"{source_description} returned an empty response",
            summary="The telemetry source returned no data.",
        )
    lowered = stripped[:200].lower()
    if lowered.startswith(("<!doctype html", "<html")):
        raise SourceFormatError(
            f"{source_description} returned an HTML page rather than CSV; the "
            "Sheet is probably not publicly readable",
            summary="The telemetry source did not return CSV data.",
        )
    return text


def fetch_csv_text(url, config=None, opener=None, setting="telemetry URL"):
    """Download CSV text over HTTP, raising structured errors on failure.

    ``opener`` is injectable so tests never touch the network.
    """
    config = config or IngestionConfig()
    export_url = normalize_sheet_url(url, setting=setting)
    open_url = opener or urlopen
    try:
        with open_url(export_url, timeout=config.http_timeout_seconds) as response:
            payload = response.read(config.max_response_bytes + 1)
    except ValueError as error:
        raise ConfigurationError(
            f"{setting} is not usable: {error}",
            summary="The configured telemetry URL is not a valid URL.",
        ) from error
    except HTTPError as error:
        raise SourceUnavailableError(
            f"the telemetry source responded with HTTP {error.code} {error.reason}",
        ) from error
    except (URLError, TimeoutError, OSError) as error:
        raise SourceUnavailableError(
            f"the telemetry source could not be reached: {error}"
        ) from error

    if len(payload) > config.max_response_bytes:
        raise SourceFormatError(
            f"the telemetry source returned more than "
            f"{config.max_response_bytes} bytes",
            summary="The telemetry source returned an unexpectedly large response.",
        )
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeError as error:
        raise SourceFormatError(
            f"the telemetry source is not valid UTF-8: {error}"
        ) from error
    return _validate_csv_text(text, "the telemetry source")


def read_local_csv_text(path):
    """Read a development CSV from disk, with the same structured errors."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise SourceUnavailableError(
            f"the local development CSV could not be read: {error}",
            summary="The configured local telemetry file could not be read.",
        ) from error
    return _validate_csv_text(text, "the local development CSV")


def retrieve_csv_text(source, config=None, opener=None):
    """Fetch CSV text for a resolved source, remote or local."""
    config = config or IngestionConfig()
    if source.kind == "local":
        return read_local_csv_text(source.path)
    return fetch_csv_text(
        source.url, config=config, opener=opener, setting=source.setting
    )
