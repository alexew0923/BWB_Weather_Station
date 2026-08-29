"""Shared health-check logic for Better With Bees StationWatch Live.

This module is the single source of truth for how telemetry freshness is
retrieved, measured, and classified. It contains no presentation code, so the
terminal CLI (``station_watch.py``) and the Streamlit dashboard (``app.py``)
both render the same result object instead of repeating the calculation.

The only observation point is the public Google Sheet. A failure to observe it
is a ``MonitorError``, never an ``OFFLINE`` station status.
"""

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from zoneinfo import ZoneInfo


CSV_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1iJzvixnEx5QH2lkQNkN8xKZqpIyGO7FmEsa_qsyHCOI/export?format=csv&gid=0"
)
HALIFAX = ZoneInfo("America/Halifax")
TIMESTAMP_COLUMN = "Timestamp"
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


class MonitorError(Exception):
    """StationWatch could not determine the telemetry status.

    Raised when the telemetry source cannot be retrieved or understood. This is
    deliberately distinct from an ``OFFLINE`` status: OFFLINE means the source
    was read successfully and the newest telemetry in it is stale.

    ``summary`` is the headline shown to a person; ``detail`` is the specific
    cause. The default summary covers the common case of a failed download.
    """

    DEFAULT_SUMMARY = "StationWatch could not retrieve the telemetry source."

    def __init__(self, detail, summary=None):
        super().__init__(detail)
        self.detail = detail
        self.summary = summary or self.DEFAULT_SUMMARY


UNREADABLE_SUMMARY = "StationWatch could not make sense of the telemetry source."


class Status(Enum):
    """A telemetry-delivery classification, plus how to describe it."""

    HEALTHY = "HEALTHY"
    DELAYED = "DELAYED"
    OFFLINE = "OFFLINE"

    def __str__(self):
        return self.value

    def describe(self, age_text):
        """Return a plain sentence about telemetry delivery, not about hardware."""
        if self is Status.HEALTHY:
            return "Fresh telemetry is reaching Google Sheets."
        if self is Status.DELAYED:
            return "Telemetry is arriving later than expected."
        return f"Fresh telemetry has not reached Google Sheets for {age_text}."


@dataclass(frozen=True)
class Thresholds:
    """The freshness limits that define each status.

    ``expected_interval_minutes`` documents how often the station is expected to
    sample; it is displayed for context and is not used in classification.
    """

    healthy_max_minutes: float = 10
    offline_min_minutes: float = 30
    expected_interval_minutes: float = 5

    def classify(self, age_minutes):
        """Return the Status for a telemetry age given in minutes."""
        if age_minutes <= self.healthy_max_minutes:
            return Status.HEALTHY
        if age_minutes < self.offline_min_minutes:
            return Status.DELAYED
        return Status.OFFLINE


@dataclass(frozen=True)
class HealthReport:
    """The result of one successful health check."""

    status: Status
    latest_timestamp: datetime
    checked_at: datetime
    age_seconds: float
    thresholds: Thresholds
    recent_timestamps: tuple = field(default=())

    @property
    def age_text(self):
        """Human-readable telemetry age, for example ``1h 37m``."""
        return format_duration(self.age_seconds)

    @property
    def summary(self):
        """One sentence describing telemetry delivery."""
        return self.status.describe(self.age_text)

    def recent_gaps(self, limit=30):
        """Return the most recent ``(arrival, gap_minutes)`` inter-arrival pairs.

        The gap is the time between a reading and the reading before it, so it
        answers "has telemetry been arriving at its normal interval lately?"
        """
        window = self.recent_timestamps[-(limit + 1):]
        return [
            (later, (later - earlier).total_seconds() / 60)
            for earlier, later in zip(window, window[1:])
        ]


class TelemetrySource:
    """The public Google Sheet, read as CSV.

    Subclass or replace this to observe a different source (or to supply fixed
    readings in a test) without touching the health logic.
    """

    def __init__(self, url=CSV_URL, timezone=HALIFAX, timeout=15):
        self.url = url
        self.timezone = timezone
        self.timeout = timeout

    def read_timestamps(self):
        """Return every valid telemetry timestamp, oldest first."""
        return self.parse_timestamps(self.download())

    def download(self):
        """Return the CSV text, or raise MonitorError if it cannot be fetched."""
        try:
            with urlopen(self.url, timeout=self.timeout) as response:
                return response.read().decode("utf-8-sig")
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeError) as error:
            raise MonitorError(f"could not retrieve the telemetry source: {error}") from error

    def parse_timestamps(self, csv_text):
        """Return sorted timestamps from CSV text, skipping unparseable rows."""
        try:
            rows = csv.DictReader(io.StringIO(csv_text))
            if not rows.fieldnames or TIMESTAMP_COLUMN not in rows.fieldnames:
                raise MonitorError(
                    f"the telemetry source has no '{TIMESTAMP_COLUMN}' column",
                    summary=UNREADABLE_SUMMARY,
                )
            timestamps = []
            for row in rows:
                value = (row.get(TIMESTAMP_COLUMN) or "").strip()
                try:
                    moment = datetime.strptime(value, TIMESTAMP_FORMAT)
                except ValueError:
                    continue
                timestamps.append(moment.replace(tzinfo=self.timezone))
        except csv.Error as error:
            raise MonitorError(
                f"could not parse the telemetry source: {error}", summary=UNREADABLE_SUMMARY
            ) from error

        if not timestamps:
            raise MonitorError(
                "the telemetry source contains no valid readings",
                summary=(
                    "StationWatch reached the telemetry source, but it holds no "
                    "readings to measure."
                ),
            )
        return sorted(timestamps)


class StationMonitor:
    """Turns telemetry timestamps into a HealthReport."""

    def __init__(self, source=None, thresholds=None, timezone=HALIFAX):
        self.source = source or TelemetrySource(timezone=timezone)
        self.thresholds = thresholds or Thresholds()
        self.timezone = timezone

    def now(self):
        """The current Halifax-local time. Overridable in tests."""
        return datetime.now(self.timezone)

    def check(self, recent_limit=50):
        """Perform one check and return a HealthReport, or raise MonitorError."""
        timestamps = self.source.read_timestamps()
        checked_at = self.now()
        latest = timestamps[-1]
        age_seconds = (checked_at - latest).total_seconds()
        if age_seconds < -60:
            raise MonitorError(
                "the newest telemetry timestamp is in the future", summary=UNREADABLE_SUMMARY
            )

        age_seconds = max(0.0, age_seconds)
        return HealthReport(
            status=self.thresholds.classify(age_seconds / 60),
            latest_timestamp=latest,
            checked_at=checked_at,
            age_seconds=age_seconds,
            thresholds=self.thresholds,
            recent_timestamps=tuple(timestamps[-recent_limit:]),
        )


def check_station_health(**kwargs):
    """Convenience wrapper: one check with the default source and thresholds."""
    return StationMonitor().check(**kwargs)


def format_duration(seconds):
    """Format a duration without misleading precision: ``16m``, ``1h 37m``."""
    seconds = max(0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s" if seconds else f"{minutes}m"
    return f"{seconds}s"


def format_timestamp(moment):
    """Format a Halifax-local timestamp, for example ``2026-08-29 00:12 ADT``."""
    return moment.strftime("%Y-%m-%d %H:%M %Z")


OBSERVATION_NOTE = (
    "StationWatch currently monitors the Google Sheets endpoint. An OFFLINE state "
    "means fresh telemetry is no longer reaching Sheets; it does not yet determine "
    "which upstream component failed."
)

UPSTREAM_DOMAINS = (
    "sensor/device operation",
    "transmitter",
    "ESP-NOW link",
    "receiver",
    "Wi-Fi",
    "Apps Script upload",
    "Google Sheets ingestion",
)
