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
import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone as _timezone
from enum import Enum
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from zoneinfo import ZoneInfo


# The telemetry URL identifies a specific Sheet, so it is supplied by the
# environment and never committed. Everything below it is ordinary configuration
# that belongs in source control.
SHEET_URL_VARIABLE = "STATIONWATCH_SHEET_URL"
ENV_FILE = Path(__file__).with_name(".env")

HALIFAX = ZoneInfo("America/Halifax")
UTC = _timezone.utc
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


class ConfigurationError(MonitorError):
    """The telemetry source has not been configured.

    A missing setting is still a reason StationWatch cannot observe anything, so
    it behaves like any other monitor error rather than becoming a station status.
    """


UNREADABLE_SUMMARY = "StationWatch could not make sense of the telemetry source."


def load_env_file(path=None):
    """Read ``KEY=value`` lines from a local .env file into the environment.

    Real environment variables win, so an exported value is never overwritten.
    Absent or unreadable files are ignored: .env is optional.
    """
    try:
        text = (path or ENV_FILE).read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def sheet_url():
    """Return the configured telemetry URL, or raise ConfigurationError."""
    load_env_file()
    url = (os.environ.get(SHEET_URL_VARIABLE) or "").strip()
    if not url:
        raise ConfigurationError(
            f"{SHEET_URL_VARIABLE} is not set; copy .env.example to .env and fill it in",
            summary=f"StationWatch has no telemetry source configured ({SHEET_URL_VARIABLE}).",
        )
    return url


# --------------------------------------------------------------------------
# Operating schedule
# --------------------------------------------------------------------------
#
# The site loses building power overnight, so silence between 23:00 and 06:00 is
# expected behaviour and not a fault. Without this, StationWatch reports OFFLINE
# for seven hours every night -- roughly 29% of all wall-clock time -- and an
# alert that is wrong a third of the day is an alert nobody reads.
#
# The window and its start date are the same facts the reliability audit encodes
# in analysis/reliability-audit/audit_config.py (OPERATING_REGIMES). They are
# duplicated rather than shared because StationWatch deliberately depends on
# nothing outside the standard library. If the schedule changes, both must move.
#
# LIMITATION: this schedule is a configured assumption, not a measurement. The
# hours come from the project README and from the audit's empirically-pinned
# changeover date, not from any machine-readable configuration provided by the
# school. StationWatch does NOT verify the schedule against recent traffic the
# way the reliability audit's verify_baseline_regimes() does, so if the powered
# window is narrowed it will report OFFLINE during the new dark period until
# these values are updated by hand. The reverse error is covered: telemetry that
# genuinely arrives during a supposedly inactive window is reported on its own
# merits rather than being suppressed (see StationMonitor.classify).

CONTINUOUS = "continuous"


@dataclass(frozen=True)
class OperatingWindow:
    """A daily powered window, in local wall-clock hours."""

    starts_on: date
    open_hour: int
    close_hour: int          # exclusive; 24 means the window never closes
    label: str

    @property
    def is_continuous(self):
        return self.open_hour == 0 and self.close_hour >= 24


# Ordered oldest first. Each entry applies from its start date until the next.
OPERATING_WINDOWS = (
    OperatingWindow(date.min, 0, 24, CONTINUOUS),
    OperatingWindow(date(2026, 4, 21), 6, 23, "06:00-23:00"),
)


@dataclass(frozen=True)
class OperatingSchedule:
    """When telemetry is expected at all."""

    windows: tuple = OPERATING_WINDOWS
    timezone: object = None

    def _zone(self):
        return self.timezone or HALIFAX

    def window_for(self, moment):
        """The OperatingWindow in force on the local date of ``moment``."""
        day = moment.astimezone(self._zone()).date()
        chosen = self.windows[0]
        for window in self.windows:
            if day >= window.starts_on:
                chosen = window
        return chosen

    def _bounds(self, day, window):
        """The powered (open, close) pair on ``day`` as aware datetimes.

        00:00, 06:00 and 23:00 never fall in a DST gap or repeat under Atlantic
        time (the transition is at 02:00), so attaching the zone is safe here.
        """
        zone = self._zone()
        open_at = datetime.combine(day, time(window.open_hour), tzinfo=zone)
        if window.close_hour >= 24:
            close_at = datetime.combine(day + timedelta(days=1), time(0), tzinfo=zone)
        else:
            close_at = datetime.combine(day, time(window.close_hour), tzinfo=zone)
        return open_at, close_at

    def is_inactive(self, moment):
        """True when no telemetry is expected right now."""
        window = self.window_for(moment)
        if window.is_continuous:
            return False
        open_at, close_at = self._bounds(moment.astimezone(self._zone()).date(), window)
        return not (open_at <= moment < close_at)

    def opened_at(self, moment):
        """When the current powered window opened, or None under a continuous regime."""
        window = self.window_for(moment)
        if window.is_continuous:
            return None
        open_at, _ = self._bounds(moment.astimezone(self._zone()).date(), window)
        return open_at

    def resumes_at(self, moment):
        """The next time telemetry is expected, or None if it already is."""
        if not self.is_inactive(moment):
            return None
        zone = self._zone()
        day = moment.astimezone(zone).date()
        for offset in (0, 1):
            candidate_day = day + timedelta(days=offset)
            window = self.window_for(
                datetime.combine(candidate_day, time(12), tzinfo=zone)
            )
            if window.is_continuous:
                continue
            open_at, _ = self._bounds(candidate_day, window)
            if open_at > moment:
                return open_at
        return None

    def describe(self, moment):
        """The expected operating window on the local date of ``moment``."""
        window = self.window_for(moment)
        if window.is_continuous:
            return "continuous (no scheduled shutdown)"
        return f"{window.label} {self._zone().key}"


def localize_wall_clock(naive, timezone, reference=None):
    """Attach a timezone to a naive local timestamp, resolving DST ambiguity.

    The Sheet stores local wall-clock text with no UTC offset, so on the annual
    Atlantic fall-back the same written time occurs twice and on the
    spring-forward it never occurs at all. ``replace(tzinfo=...)`` alone silently
    picks the first interpretation, which makes a reading look up to an hour
    older than it is -- a false OFFLINE once a year, and a false HEALTHY the
    other way in spring.

    When ``reference`` (normally "now") is supplied the ambiguity is resolved
    exactly: pick the latest interpretation that is not in the future. Without a
    reference the earlier one is kept, which errs towards reporting telemetry as
    stale rather than fresh.

    Returns ``(utc_datetime, was_ambiguous)``. The result is UTC on purpose:
    subtracting two aware datetimes that share one tzinfo object makes Python
    ignore the zone and subtract the wall clocks, so keeping local-aware values
    around would reintroduce the very DST error this function exists to remove.
    Convert back with format_timestamp for display.
    """
    first = naive.replace(tzinfo=timezone, fold=0).astimezone(UTC)
    second = naive.replace(tzinfo=timezone, fold=1).astimezone(UTC)
    if first == second:
        return first, False
    if reference is None:
        # Without a clock to compare against, keep the earlier instant: it makes
        # telemetry look older, which is the safer direction to be wrong in.
        return first, True
    plausible = [moment for moment in (first, second) if moment <= reference]
    if not plausible:
        # Both readings are in the future; hand back the nearer one and let the
        # caller decide that the source is unusable.
        return min(first, second), True
    return max(plausible), True


class Status(Enum):
    """A telemetry-delivery classification, plus how to describe it."""

    HEALTHY = "HEALTHY"
    AWAITING_TELEMETRY = "AWAITING TELEMETRY"
    DELAYED = "DELAYED"
    OFFLINE = "OFFLINE"
    SCHEDULED_INACTIVE = "SCHEDULED INACTIVE"

    def __str__(self):
        return self.value

    def describe(self, age_text):
        """Return a plain sentence about telemetry delivery, not about hardware."""
        if self is Status.HEALTHY:
            return "Fresh telemetry is reaching Google Sheets."
        if self is Status.AWAITING_TELEMETRY:
            # The powered window has reopened but nothing has arrived yet. Saying
            # HEALTHY here would assert fresh telemetry that does not exist, and
            # a station that failed overnight would be described as working every
            # morning for the length of the startup grace.
            return (
                f"The operating window has reopened and the first reading has not "
                f"arrived yet. The newest telemetry is {age_text} old."
            )
        if self is Status.DELAYED:
            return "Telemetry is arriving later than expected."
        if self is Status.SCHEDULED_INACTIVE:
            # Says nothing about the station's condition on purpose: site power
            # is off, so a healthy station and a failed one look identical.
            return (
                "Telemetry is not expected right now: the site is outside its "
                "scheduled operating window. Station condition is unknown."
            )
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
    # Grace after site power returns: the station has to boot, associate and
    # complete a sampling cycle before its silence means anything.
    startup_grace_minutes: float = 15

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
    # The window telemetry is expected in, e.g. "06:00-23:00 America/Halifax".
    window_text: str = "continuous (no scheduled shutdown)"
    # When telemetry is next expected; set only while SCHEDULED_INACTIVE.
    resumes_at: datetime = None
    # Age the status was classified on. Differs from age_seconds just after the
    # powered window opens, where the startup grace applies.
    classification_age_seconds: float = 0.0
    # True when the newest timestamp fell in a repeated/skipped DST hour.
    latest_is_ambiguous: bool = False

    @property
    def age_text(self):
        """Human-readable telemetry age, for example ``1h 37m``."""
        return format_duration(self.age_seconds)

    @property
    def summary(self):
        """One sentence describing telemetry delivery."""
        return self.status.describe(self.age_text)

    @property
    def resumes_text(self):
        """When telemetry is next expected, or an em dash if it already is."""
        return format_timestamp(self.resumes_at) if self.resumes_at else "\u2014"

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

    def __init__(self, url=None, timezone=HALIFAX, timeout=15):
        # Left unresolved until it is needed, so building a source never fails
        # and a missing setting surfaces as an ordinary monitor error.
        self._url = url
        self.timezone = timezone
        self.timeout = timeout
        # Timestamps that landed in a repeated or skipped DST hour, recorded by
        # parse_timestamps so the caller can say so rather than imply certainty.
        self.ambiguous = set()

    @property
    def url(self):
        """The configured telemetry URL, read from the environment on demand."""
        return self._url or sheet_url()

    def read_timestamps(self, reference=None):
        """Return every valid telemetry timestamp, oldest first.

        ``reference`` is the current time, used to resolve DST-ambiguous
        wall-clock timestamps; see localize_wall_clock.
        """
        return self.parse_timestamps(self.download(), reference=reference)

    def download(self):
        """Return the CSV text, or raise MonitorError if it cannot be fetched."""
        url = self.url
        try:
            with urlopen(url, timeout=self.timeout) as response:
                return response.read().decode("utf-8-sig")
        except ValueError as error:
            # urlopen raises ValueError for a URL with no usable scheme, e.g. a
            # typo in .env. That is a configuration fault, and configuration
            # faults are monitor errors here -- never a station status, and never
            # a traceback in front of a user.
            raise MonitorError(
                f"the configured telemetry URL is not usable: {error}",
                summary=(
                    f"StationWatch's telemetry URL is not a valid URL "
                    f"({SHEET_URL_VARIABLE})."
                ),
            ) from error
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeError) as error:
            raise MonitorError(f"could not retrieve the telemetry source: {error}") from error

    def parse_timestamps(self, csv_text, reference=None):
        """Return sorted timestamps from CSV text, skipping unparseable rows.

        Timestamps are local wall-clock text with no UTC offset, so each one is
        localised through localize_wall_clock rather than having a zone attached
        blindly.
        """
        try:
            rows = csv.DictReader(io.StringIO(csv_text))
            if not rows.fieldnames or TIMESTAMP_COLUMN not in rows.fieldnames:
                raise MonitorError(
                    f"the telemetry source has no '{TIMESTAMP_COLUMN}' column",
                    summary=UNREADABLE_SUMMARY,
                )
            timestamps = []
            self.ambiguous = set()
            for row in rows:
                value = (row.get(TIMESTAMP_COLUMN) or "").strip()
                try:
                    naive = datetime.strptime(value, TIMESTAMP_FORMAT)
                except ValueError:
                    continue
                moment, was_ambiguous = localize_wall_clock(
                    naive, self.timezone, reference
                )
                if was_ambiguous:
                    self.ambiguous.add(moment)
                timestamps.append(moment)
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

    def __init__(self, source=None, thresholds=None, timezone=HALIFAX, schedule=None):
        self.source = source or TelemetrySource(timezone=timezone)
        self.thresholds = thresholds or Thresholds()
        self.timezone = timezone
        self.schedule = schedule or OperatingSchedule(timezone=timezone)

    def now(self):
        """The current instant, in UTC. Overridable in tests.

        UTC rather than Halifax so that every subtraction against a telemetry
        timestamp is a real elapsed duration; see localize_wall_clock.
        """
        return datetime.now(UTC)

    def check(self, recent_limit=50):
        """Perform one check and return a HealthReport, or raise MonitorError."""
        # Read the clock first: it resolves DST-ambiguous source timestamps.
        checked_at = self.now()
        timestamps = self.source.read_timestamps(reference=checked_at)
        latest = timestamps[-1]
        age_seconds = (checked_at - latest).total_seconds()
        if age_seconds < -60:
            raise MonitorError(
                "the newest telemetry timestamp is in the future", summary=UNREADABLE_SUMMARY
            )

        age_seconds = max(0.0, age_seconds)
        status, classification_age = self.classify(latest, checked_at)

        return HealthReport(
            status=status,
            latest_timestamp=latest,
            checked_at=checked_at,
            age_seconds=age_seconds,
            thresholds=self.thresholds,
            recent_timestamps=tuple(timestamps[-recent_limit:]),
            window_text=self.schedule.describe(checked_at),
            resumes_at=self.schedule.resumes_at(checked_at),
            classification_age_seconds=classification_age,
            latest_is_ambiguous=latest in getattr(self.source, "ambiguous", ()),
        )

    def classify(self, latest, checked_at):
        """Return ``(status, classification_age_seconds)`` for one observation.

        Stale telemetry is only a fault while telemetry is expected. Outside the
        powered window the status is SCHEDULED_INACTIVE, which asserts nothing
        about the station -- with site power off, a healthy station and a failed
        one are indistinguishable.

        Just after the window reopens the newest reading is still hours old
        through no fault of the station, so the age is measured from the end of
        the startup grace instead. That decays naturally: the grace buys a fixed
        amount of silence, after which the ordinary thresholds take over.
        """
        if self.schedule.is_inactive(checked_at):
            # The window is an assumption, and telemetry that is genuinely
            # arriving is evidence against it. Reporting SCHEDULED INACTIVE over
            # fresh data would hide a schedule change behind the very rule the
            # change invalidates, so freshness wins here.
            age_seconds = max(0.0, (checked_at - latest).total_seconds())
            if age_seconds / 60 <= self.thresholds.healthy_max_minutes:
                return Status.HEALTHY, age_seconds
            return Status.SCHEDULED_INACTIVE, 0.0

        opened_at = self.schedule.opened_at(checked_at)
        reference = latest
        if opened_at is not None:
            grace_until = opened_at + timedelta(
                minutes=self.thresholds.startup_grace_minutes
            )
            reference = max(latest, grace_until)

        classification_age = max(0.0, (checked_at - reference).total_seconds())
        status = self.thresholds.classify(classification_age / 60)

        # The startup grace may be the only reason this is not DELAYED or
        # OFFLINE. In that case the telemetry itself is still stale, so report
        # that plainly instead of borrowing the HEALTHY label from the grace.
        if status is Status.HEALTHY:
            true_age_minutes = (checked_at - latest).total_seconds() / 60
            if true_age_minutes > self.thresholds.healthy_max_minutes:
                return Status.AWAITING_TELEMETRY, classification_age

        return status, classification_age


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


def format_timestamp(moment, timezone=HALIFAX):
    """Render an instant in station-local time, e.g. ``2026-08-29 00:12 ADT``.

    Instants are carried in UTC internally, so display is the one place the
    local zone is applied.
    """
    return moment.astimezone(timezone).strftime("%Y-%m-%d %H:%M %Z")


OBSERVATION_NOTE = (
    "StationWatch currently monitors the Google Sheets endpoint. An OFFLINE state "
    "means fresh telemetry is no longer reaching Sheets; it does not yet determine "
    "which upstream component failed. SCHEDULED INACTIVE means the site is outside "
    "its powered window, so no telemetry is due -- it is not a statement that the "
    "station is healthy, because with site power off a working station and a failed "
    "one look exactly the same."
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
