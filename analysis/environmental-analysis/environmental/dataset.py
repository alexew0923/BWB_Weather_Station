"""Stage 2: the canonical environmental dataset.

Everything above this module works with spreadsheet rows. Everything below it
works with an ``EnvironmentalDataset``: a sorted, deduplicated, timezone-aware
table of canonically named sensor columns, with a parallel mask saying which
individual readings may be interpreted.

Two decisions are worth stating plainly.

**Timestamps are carried as ``America/Halifax``-aware pandas timestamps.**
The source stores local wall-clock text with no offset. pandas stores an aware
datetime as a UTC instant with a display zone attached, so differences between
two aware timestamps are real elapsed time even across a DST transition -- the
trap that bites plain ``datetime`` objects does not apply here. Keeping the
local zone means hour-of-day analysis, which is genuinely about local solar
time, does not need converting back and forth.

**Invalid readings are masked, not deleted.** A physically impossible value is
a third state, distinct from "row missing" and "field populated". Deleting it
would silently move it into the missing bucket and change every completeness
figure. It is masked out of analysis and counted in the dataset's report.
"""

import io
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import sensors
from ._reliability_bridge import (
    MAX_PLAUSIBLE_COUNT,
    localize_timestamps,
    scheduled_transmissions_between,
)
from .config import PLAUSIBLE_RANGES, EnvironmentalConfig
from .data_sources import SourceReference, resolve_historical_source, retrieve_csv_text
from .errors import EmptyDatasetError, SchemaError, SourceFormatError
from .version import DATASET_SCHEMA_VERSION

#: Reason codes for known device failure signatures. Kept short and few on
#: purpose: this is a record of the two patterns this deployment actually
#: produces, not a general fault taxonomy.
SHT4X_FAULT = "known_sht40_fault_signature"
MULTI_SENSOR_FAULT = "known_multi_sensor_fault_signature"


@dataclass(frozen=True)
class IngestionReport:
    """What happened while turning a CSV into a dataset.

    Every number here is a defect count. They are surfaced rather than logged
    so a frontend can show "1 023 rows had no pressure" instead of a user
    wondering why a chart has holes.
    """

    rows_read: int = 0
    rows_kept: int = 0
    unparseable_timestamps: int = 0
    duplicate_timestamps: int = 0
    corrupt_frames: int = 0
    #: Frames carrying a known instrument failure signature. Distinct from
    #: ``corrupt_frames`` (a damaged buffer) and from ``implausible_values`` (a
    #: number outside its physical range): these readings are inside every
    #: plausible range and are still not measurements.
    sensor_fault_frames: int = 0
    #: Breakdown of the above by reason code, e.g.
    #: ``{"known_sht40_fault_signature": 74}``.
    sensor_fault_signatures: dict = field(default_factory=dict)
    non_numeric_cells: dict = field(default_factory=dict)
    implausible_values: dict = field(default_factory=dict)
    ambiguous_dst_timestamps: int = 0
    missing_optional_columns: tuple = ()
    notes: tuple = ()

    def to_dict(self):
        return {
            "rows_read": self.rows_read,
            "rows_kept": self.rows_kept,
            "unparseable_timestamps": self.unparseable_timestamps,
            "duplicate_timestamps": self.duplicate_timestamps,
            "corrupt_frames": self.corrupt_frames,
            "sensor_fault_frames": self.sensor_fault_frames,
            "sensor_fault_signatures": dict(self.sensor_fault_signatures),
            "non_numeric_cells": dict(self.non_numeric_cells),
            "implausible_values": dict(self.implausible_values),
            "ambiguous_dst_timestamps": self.ambiguous_dst_timestamps,
            "missing_optional_columns": list(self.missing_optional_columns),
            "notes": list(self.notes),
        }


class EnvironmentalDataset:
    """A validated environmental time series.

    The object is treated as immutable by every analysis stage: ``subset``
    returns a new dataset rather than mutating this one.
    """

    def __init__(self, frame, valid, source, config, report, fault_reasons=None):
        self._frame = frame
        self._valid = valid
        self.source = source
        self.config = config
        self.report = report
        self.schema_version = DATASET_SCHEMA_VERSION
        self._fault_reasons = (
            pd.Series("", index=frame.index, dtype="object")
            if fault_reasons is None
            else fault_reasons
        )

    @property
    def fault_reasons(self):
        """Per-row device-fault reason code; empty string where none applies.

        Exposed so a verification run can say *why* a reading was excluded
        rather than only that it was.
        """
        return self._fault_reasons

    # -- basic shape -------------------------------------------------------

    def __len__(self):
        return len(self._frame)

    def __repr__(self):
        if self._frame.empty:
            return "<EnvironmentalDataset empty>"
        return (
            f"<EnvironmentalDataset rows={len(self._frame)} "
            f"{self.start_time.isoformat()} .. {self.end_time.isoformat()}>"
        )

    @property
    def frame(self):
        """The canonical frame. Treat as read-only."""
        return self._frame

    @property
    def validity(self):
        """Boolean mask, one column per sensor: may this reading be used?"""
        return self._valid

    @property
    def timestamps(self):
        return self._frame.index

    @property
    def number_of_rows(self):
        return len(self._frame)

    @property
    def start_time(self):
        return None if self._frame.empty else self._frame.index[0]

    @property
    def end_time(self):
        return None if self._frame.empty else self._frame.index[-1]

    @property
    def sensor_columns(self):
        """Canonical sensor columns actually present in this dataset."""
        return tuple(c for c in sensors.SENSOR_COLUMNS if c in self._frame.columns)

    @property
    def available_sensors(self):
        """Sensors that carry at least one interpretable observation."""
        return tuple(c for c in self.sensor_columns if self._valid[c].any())

    # -- accessors ---------------------------------------------------------

    def series(self, sensor, valid_only=True):
        """Return one sensor as a Series, with unusable readings masked to NaN.

        This is the only way analysis code should reach sensor values. Reading
        ``dataset.frame[column]`` directly bypasses the quality masks and can
        interpret a corrupt reading as an environmental measurement.
        """
        if sensor not in self._frame.columns:
            return pd.Series(dtype="float64", index=self._frame.index, name=sensor)
        values = self._frame[sensor]
        if not valid_only:
            return values
        return values.where(self._valid[sensor])

    def valid_fraction(self, sensor):
        """Fraction of received rows whose reading for ``sensor`` is usable.

        The denominator is received rows, never scheduled slots: a row that
        never arrived is a delivery fact and belongs to the reliability audit.
        """
        if self._frame.empty or sensor not in self._frame.columns:
            return 0.0
        return float(self._valid[sensor].mean())

    def valid_count(self, sensor):
        if sensor not in self._frame.columns:
            return 0
        return int(self._valid[sensor].sum())

    def subset(self, start=None, end=None):
        """Return the closed interval ``[start, end]`` as a new dataset."""
        frame = self._frame
        valid = self._valid
        if start is not None:
            keep = frame.index >= pd.Timestamp(start)
            frame, valid = frame[keep], valid[keep]
        if end is not None:
            keep = frame.index <= pd.Timestamp(end)
            frame, valid = frame[keep], valid[keep]
        return EnvironmentalDataset(
            frame, valid, self.source, self.config, self.report,
            self._fault_reasons.reindex(frame.index),
        )

    # -- cadence and coverage ---------------------------------------------

    def inter_arrival_minutes(self):
        """Minutes between consecutive received rows."""
        if len(self._frame) < 2:
            return pd.Series(dtype="float64")
        return self._frame.index.to_series().diff().dt.total_seconds().div(60).iloc[1:]

    def sampling_statistics(self):
        """Observed cadence, as measurements rather than assumptions."""
        gaps = self.inter_arrival_minutes()
        if gaps.empty:
            return {
                "rows": len(self._frame),
                "nominal_cycle_minutes": self.config.quality.nominal_cycle_minutes,
            }
        continuity = self.config.quality.continuity_gap_minutes
        return {
            "rows": len(self._frame),
            "nominal_cycle_minutes": self.config.quality.nominal_cycle_minutes,
            "median_interval_minutes": float(gaps.median()),
            "p90_interval_minutes": float(gaps.quantile(0.90)),
            "p99_interval_minutes": float(gaps.quantile(0.99)),
            "max_interval_minutes": float(gaps.max()),
            "sub_minute_repeats": int(
                (gaps < self.config.quality.min_inter_arrival_minutes).sum()
            ),
            "gaps_over_continuity": int((gaps > continuity).sum()),
            "gaps_over_outage_threshold": int(
                (gaps > self.config.quality.outage_gap_minutes).sum()
            ),
        }

    def expected_observations(self, start=None, end=None):
        """Scheduled transmissions in an interval, from the audit's schedule.

        Uses the reliability audit's operating regimes, so coverage figures
        here agree with every other coverage figure in the repository.
        """
        start = self.start_time if start is None else pd.Timestamp(start)
        end = self.end_time if end is None else pd.Timestamp(end)
        if start is None or end is None or end <= start:
            return 0
        return int(scheduled_transmissions_between(start.to_pydatetime(), end.to_pydatetime()))

    def coverage(self, start=None, end=None):
        """Telemetry coverage of an interval: received rows / scheduled slots."""
        window = self.subset(start, end)
        expected = self.expected_observations(
            start if start is not None else window.start_time,
            end if end is not None else window.end_time,
        )
        received = len(window)
        fraction = None if not expected else min(1.0, received / expected)
        gaps = window.inter_arrival_minutes()
        return {
            "start_time": window.start_time,
            "end_time": window.end_time,
            "received": received,
            "expected": expected or None,
            "fraction": fraction,
            "longest_gap_minutes": float(gaps.max()) if not gaps.empty else None,
        }

    def gap_boundaries(self, minutes=None):
        """Indices at which a telemetry gap breaks signal continuity."""
        minutes = (
            self.config.quality.continuity_gap_minutes if minutes is None else minutes
        )
        gaps = self.inter_arrival_minutes()
        return gaps[gaps > minutes]

    # -- reporting ---------------------------------------------------------

    def describe(self):
        """A serialisable description of the dataset itself."""
        return {
            "station_id": self.config.station_id,
            "schema_version": self.schema_version,
            "source": self.source.to_dict() if self.source else None,
            "rows": len(self._frame),
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "timezone": str(self.config.ingestion.timezone),
            "sensors": {
                name: {
                    "valid_observations": self.valid_count(name),
                    "valid_fraction": self.valid_fraction(name),
                }
                for name in self.sensor_columns
            },
            "sampling": self.sampling_statistics(),
            "coverage": _serialise_coverage(self.coverage()),
            "ingestion": self.report.to_dict(),
        }


def _serialise_coverage(coverage):
    out = dict(coverage)
    for key in ("start_time", "end_time"):
        value = out.get(key)
        out[key] = value.isoformat() if value is not None else None
    return out


# --------------------------------------------------------------------------
# Construction
# --------------------------------------------------------------------------


_AMBIGUOUS_PATTERN = re.compile(r"(\d+) ambiguous timestamp")


def _count_ambiguous(log_lines):
    """Number of DST-ambiguous timestamps, read back from the audit's log.

    ``localize_timestamps`` reports its findings through the log list it is
    handed rather than returning them, so the count is recovered from that
    line. The audit's private ambiguity mask is deliberately not reached into.
    """
    for line in log_lines:
        match = _AMBIGUOUS_PATTERN.search(line)
        if match:
            return int(match.group(1))
    return 0


def device_fault_signatures(canonical, quality):
    """Label rows carrying a known instrument failure signature.

    This is stage 3 of the quality pipeline and is deliberately distinct from
    stage 2, physical plausibility. Plausibility asks "could this number be a
    measurement of this quantity at all"; this asks the narrower question "does
    this frame carry a pattern a known part emits when it has failed". A value
    can sit inside every physical range and still be a fault code, which is
    exactly what happens here.

    Nothing is mutated: the raw values stay in the frame and only the analytical
    validity masks change, so a defect can still be counted, charted and
    reconciled against the source export.

    Returns a Series of reason codes aligned to ``canonical``; the empty string
    means "no known signature". The more specific signature wins, so a frame is
    labelled with one reason rather than several.
    """
    reasons = pd.Series("", index=canonical.index, dtype="object")

    humidity = canonical[sensors.HUMIDITY]
    temperature = canonical[sensors.TEMPERATURE]
    pressure = canonical[sensors.PRESSURE]

    # -- most specific first: two separate parts reporting zero together ------
    if quality.treat_multi_sensor_zero_frames_as_invalid:
        multi = (
            (temperature == 0.0) & (humidity == 0.0) & (pressure == 0.0)
        ).fillna(False)
        reasons[multi] = MULTI_SENSOR_FAULT
    else:
        multi = pd.Series(False, index=canonical.index)

    # -- the SHT4x pair ------------------------------------------------------
    faults = tuple(quality.sht4x_fault_humidity_values)
    if quality.treat_sht4x_fault_frames_as_invalid and faults:
        # Exact equality: these are literal repeated fault codes, not a region
        # of a continuous distribution, so a tolerance would only widen the rule
        # beyond what the evidence supports.
        #
        # The pair is required. Humidity alone is not evidence of a fault -- a
        # genuinely dry hour must not be called a broken sensor -- and
        # temperature alone is not either, which is what keeps real 0 degC
        # winter readings in the dataset.
        sht4x = (
            humidity.isin(faults) & ((temperature == 0.0) | temperature.isna())
        ).fillna(False)
        reasons[sht4x & ~multi] = SHT4X_FAULT

    return reasons


#: Channels excluded for each signature. Only the parts the evidence implicates
#: are masked; the wetness and soil channels come from other devices and are
#: left to be judged on their own.
FAULT_SIGNATURE_CHANNELS = {
    SHT4X_FAULT: (sensors.TEMPERATURE, sensors.HUMIDITY),
    MULTI_SENSOR_FAULT: (
        sensors.TEMPERATURE,
        sensors.HUMIDITY,
        sensors.PRESSURE,
    ),
}


def _resolve_timestamp_column(columns):
    for name in sensors.TIMESTAMP_COLUMN_CANDIDATES:
        if name in columns:
            return name
    raise SchemaError(
        "no timestamp column found; expected one of "
        f"{list(sensors.TIMESTAMP_COLUMN_CANDIDATES)}, got {list(columns)}",
        summary="The telemetry source has no timestamp column.",
    )


def build_dataset_from_csv_text(csv_text, config=None, source=None):
    """Parse, validate and canonicalise telemetry CSV text.

    Raises a domain error rather than letting a pandas exception escape.
    """
    config = config or EnvironmentalConfig()
    try:
        raw = pd.read_csv(io.StringIO(csv_text))
    except (pd.errors.ParserError, pd.errors.EmptyDataError, ValueError) as error:
        raise SourceFormatError(
            f"the telemetry CSV could not be parsed: {error}"
        ) from error

    if raw.empty and not len(raw.columns):
        raise SourceFormatError(
            "the telemetry CSV contains no columns",
            summary="The telemetry source returned no data.",
        )

    timestamp_column = _resolve_timestamp_column(raw.columns)
    missing_required = [
        name for name in sensors.REQUIRED_SOURCE_COLUMNS if name not in raw.columns
    ]
    if missing_required:
        raise SchemaError(
            "the telemetry source is missing required column(s): "
            + ", ".join(missing_required),
            summary="The telemetry source is missing columns the analysis requires.",
        )

    notes = []
    missing_optional = tuple(
        name
        for name in sensors.SOURCE_COLUMN_MAP
        if name not in raw.columns and name not in sensors.REQUIRED_SOURCE_COLUMNS
    )
    if missing_optional:
        notes.append(
            "columns absent from the source: " + ", ".join(missing_optional)
        )

    rows_read = len(raw)
    if rows_read == 0:
        raise EmptyDatasetError(
            "the telemetry source contains a header row and no observations",
            summary="The telemetry source holds no observations yet.",
        )
    frame = raw.reset_index(drop=True)
    frame["_file_order"] = np.arange(len(frame))

    # -- timestamps ----------------------------------------------------------
    parsed = pd.to_datetime(frame[timestamp_column], errors="coerce", format="mixed")
    unparseable = int(parsed.isna().sum())
    frame = frame.loc[parsed.notna()].copy()
    parsed = parsed.loc[parsed.notna()]
    if frame.empty:
        raise EmptyDatasetError(
            f"all {rows_read} row(s) had unparseable timestamps",
            summary="The telemetry source holds no readable observations.",
        )

    if getattr(parsed.dt, "tz", None) is not None:
        aware = parsed.dt.tz_convert(config.ingestion.timezone)
        dst_log = []
    else:
        # Reuse the reliability audit's DST resolution rather than re-deriving
        # it: it resolves the annual Atlantic fall-back from file order, which
        # is the only source of truth the export actually provides.
        dst_log = []
        aware = localize_timestamps(parsed, dst_log)
    frame["timestamp"] = aware
    ambiguous = _count_ambiguous(dst_log)
    if any("DST" in line for line in dst_log):
        notes.extend(line.strip() for line in dst_log if "DST" in line)

    # -- numeric coercion ----------------------------------------------------
    non_numeric = {}
    canonical = pd.DataFrame(index=frame.index)
    canonical["timestamp"] = frame["timestamp"]
    canonical["_file_order"] = frame["_file_order"]
    for source_name, canonical_name in sensors.SOURCE_COLUMN_MAP.items():
        if source_name not in frame.columns:
            canonical[canonical_name] = np.nan
            continue
        column = frame[source_name]
        numbers = pd.to_numeric(column, errors="coerce")
        # Only cells that held something unparseable count as coercion damage;
        # an empty cell is ordinary missing telemetry.
        held_something = column.notna() & (column.astype(str).str.strip() != "")
        damaged = int((numbers.isna() & held_something).sum())
        if damaged:
            non_numeric[canonical_name] = damaged
        canonical[canonical_name] = numbers.astype("float64")

    # -- corrupt frames ------------------------------------------------------
    # A row whose boot counter is memory garbage carries a damaged buffer, so
    # no field on it may be trusted. The ROW is kept -- it really did arrive --
    # but every value on it is nulled, exactly as the reliability audit does.
    boot = canonical[sensors.BOOT_COUNT]
    corrupt = boot.notna() & (boot > MAX_PLAUSIBLE_COUNT)
    corrupt_frames = int(corrupt.sum())
    if corrupt_frames:
        canonical.loc[corrupt, list(sensors.SENSOR_COLUMNS) + [sensors.BOOT_COUNT]] = np.nan
        notes.append(
            f"{corrupt_frames} corrupt frame(s) nulled (boot counter above "
            f"{MAX_PLAUSIBLE_COUNT})"
        )

    # -- ordering and duplicates --------------------------------------------
    canonical = canonical.sort_values(["timestamp", "_file_order"], kind="stable")
    duplicated = canonical["timestamp"].duplicated(
        keep="first" if config.ingestion.duplicate_timestamp_policy == "first" else "last"
    )
    duplicate_timestamps = int(duplicated.sum())
    canonical = canonical.loc[~duplicated]

    canonical = canonical.set_index("timestamp").drop(columns=["_file_order"])
    canonical.index.name = "timestamp"

    if canonical.empty:
        raise EmptyDatasetError(
            "no usable rows remain after validation",
            summary="The telemetry source holds no usable observations.",
        )

    # -- validity masks ------------------------------------------------------
    valid = pd.DataFrame(index=canonical.index)
    implausible = {}
    for name in sensors.SENSOR_COLUMNS:
        values = canonical[name]
        present = values.notna()
        low, high = PLAUSIBLE_RANGES.get(name, (-np.inf, np.inf))
        in_range = present & (values >= low) & (values <= high)
        out_of_range = int((present & ~in_range).sum())
        if out_of_range:
            implausible[name] = out_of_range
        mask = in_range
        if name == sensors.SOIL_SIGNAL and config.quality.treat_soil_zero_as_invalid:
            # A stored zero is ambiguous: the ingestion script blanks zero
            # values, so a zero that survived predates that behaviour and may
            # be a sentinel rather than a reading. Excluded from analysis and
            # counted separately -- never silently treated as "very dry".
            mask = mask & (values != 0)
        valid[name] = mask.fillna(False)

    # -- device fault signatures ---------------------------------------------
    # Stage 3, applied after physical plausibility and kept separate from it: a
    # fault code can sit inside every physical range and still not be a
    # measurement. Raw values are never touched -- only the analytical masks.
    fault_reasons = device_fault_signatures(canonical, config.quality)
    sensor_fault_frames = int((fault_reasons != "").sum())
    fault_signature_counts = {
        reason: int(count)
        for reason, count in fault_reasons[fault_reasons != ""]
        .value_counts()
        .items()
    }
    for reason, channels in FAULT_SIGNATURE_CHANNELS.items():
        matched = fault_reasons == reason
        if not matched.any():
            continue
        for channel in channels:
            valid.loc[matched, channel] = False
        notes.append(
            f"{int(matched.sum())} frame(s) excluded as {reason} "
            f"({', '.join(channels)})"
        )

    report = IngestionReport(
        rows_read=rows_read,
        rows_kept=len(canonical),
        unparseable_timestamps=unparseable,
        duplicate_timestamps=duplicate_timestamps,
        corrupt_frames=corrupt_frames,
        sensor_fault_frames=sensor_fault_frames,
        sensor_fault_signatures=fault_signature_counts,
        non_numeric_cells=non_numeric,
        implausible_values=implausible,
        ambiguous_dst_timestamps=ambiguous,
        missing_optional_columns=missing_optional,
        notes=tuple(notes),
    )
    return EnvironmentalDataset(
        canonical, valid, source, config, report, fault_reasons
    )


def load_environmental_dataset(
    config=None, source=None, opener=None, csv_text=None, environ=None,
    allow_local_override=True,
):
    """Load the canonical historical dataset.

    Production path::

        HISTORICAL_DATA_URL -> normalise -> HTTP fetch -> validate -> dataset

    ``csv_text`` short-circuits retrieval and is how tests and notebooks supply
    controlled input. ``opener`` replaces ``urlopen`` for the same reason.
    """
    config = config or EnvironmentalConfig()
    if csv_text is None:
        source = source or resolve_historical_source(
            config.ingestion, environ=environ, allow_local_override=allow_local_override
        )
        csv_text = retrieve_csv_text(source, config=config.ingestion, opener=opener)
    elif source is None:
        source = SourceReference(
            kind="local", label="Supplied CSV text", setting="csv_text"
        )
    return build_dataset_from_csv_text(csv_text, config=config, source=source)
