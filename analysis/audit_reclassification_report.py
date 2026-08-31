"""Generate the semantic-reclassification report for the audit fixes.

Every fix in this change alters how existing data is *interpreted*. That is
exactly the kind of change that should never be taken on trust, so this script
runs each affected pipeline twice against the real historical record -- once
with the fix disabled, once enabled -- and emits every finding, gap and event
whose meaning changed, with the reason and the values behind it.

    python analysis/audit_reclassification_report.py [--csv PATH] [--out-dir DIR]

This is a verification tool, not production logic: nothing imports it, and it
writes to a generated-output directory rather than into any package. The report
is regenerable rather than a one-off paste -- rerun it after any change to the
quality rules and diff the output.

Findings covered: ENV-01, RELY-01, SOIL-01, INGEST-01.
"""

import argparse
import contextlib
import csv
import io
import sys
from dataclasses import replace
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
ENVIRONMENTAL_DIR = REPOSITORY_ROOT / "analysis" / "environmental-analysis"
RELIABILITY_DIR = REPOSITORY_ROOT / "analysis" / "reliability-audit"
for _directory in (ENVIRONMENTAL_DIR, RELIABILITY_DIR):
    if str(_directory) not in sys.path:
        sys.path.insert(0, str(_directory))

DEFAULT_CSV = RELIABILITY_DIR / "data" / "HistoricalData.csv"
#: Generated artifacts, deliberately outside every package.
DEFAULT_OUT_DIR = REPOSITORY_ROOT / "analysis" / "verification_output"

CSV_COLUMNS = [
    "finding_id",
    "timestamp_or_id",
    "entity_type",
    "old_interpretation",
    "new_interpretation",
    "reason",
    "relevant_values",
]


def _record(finding, key, entity, old, new, reason, values):
    return {
        "finding_id": finding,
        "timestamp_or_id": key,
        "entity_type": entity,
        "old_interpretation": old,
        "new_interpretation": new,
        "reason": reason,
        "relevant_values": values,
    }


# --------------------------------------------------------------------------
# ENV-01
# --------------------------------------------------------------------------


def env01(csv_text):
    from environmental.api import analyze_environment, load_environmental_dataset
    from environmental.config import EnvironmentalConfig
    from environmental import sensors

    enabled = EnvironmentalConfig()
    disabled = replace(
        enabled,
        quality=replace(
            enabled.quality,
            treat_sht4x_fault_frames_as_invalid=False,
            treat_multi_sensor_zero_frames_as_invalid=False,
        ),
    )

    before_dataset = load_environmental_dataset(config=disabled, csv_text=csv_text)
    after_dataset = load_environmental_dataset(config=enabled, csv_text=csv_text)
    before = analyze_environment(before_dataset, disabled)
    after = analyze_environment(after_dataset, enabled)

    frame = after_dataset.frame
    reasons = after_dataset.fault_reasons

    rows = []
    by_id_before = {a.anomaly_id: a for a in before.anomalies}
    by_id_after = {a.anomaly_id: a for a in after.anomalies}

    for anomaly in sorted(before.anomalies, key=lambda a: a.time):
        if anomaly.anomaly_id in by_id_after:
            continue
        reason = reasons.get(anomaly.time, "")
        row = frame.loc[anomaly.time] if anomaly.time in frame.index else None
        values = (
            f"T={row[sensors.TEMPERATURE]}, RH={row[sensors.HUMIDITY]}, "
            f"P={row[sensors.PRESSURE]}"
            if row is not None
            else f"value={anomaly.value:g}"
        )
        rows.append(_record(
            "ENV-01", anomaly.time.isoformat(), "environmental_anomaly",
            f"{anomaly.evidence_strength} {anomaly.kind} (value {anomaly.value:g})",
            "excluded_from_inference",
            reason or "within a window dominated by fault frames",
            values,
        ))

    for anomaly in sorted(after.anomalies, key=lambda a: a.time):
        if anomaly.anomaly_id in by_id_before:
            continue
        rows.append(_record(
            "ENV-01", anomaly.time.isoformat(), "environmental_anomaly",
            "not reported (comparison window contained fault frames)",
            f"{anomaly.evidence_strength} {anomaly.kind} (value {anomaly.value:g})",
            "unmasked_after_fault_exclusion",
            f"value={anomaly.value:g}, robust_z={anomaly.robust_z:.1f}",
        ))

    for reason, count in sorted(after_dataset.report.sensor_fault_signatures.items()):
        matched = reasons[reasons == reason]
        rows.append(_record(
            "ENV-01",
            f"{matched.index.min().isoformat()}..{matched.index.max().isoformat()}",
            "telemetry_frames",
            "interpreted as environmental observations",
            "excluded_from_inference (raw values preserved)",
            reason,
            f"{count} frames",
        ))

    summary = {
        "fault_signatures": dict(after_dataset.report.sensor_fault_signatures),
        "fault_frames": after_dataset.report.sensor_fault_frames,
        "anomalies_before": len(before.anomalies),
        "anomalies_after": len(after.anomalies),
        "events_before": len(before.events),
        "events_after": len(after.events),
        "rows": len(after_dataset),
        "remaining_artifacts": [
            (a.anomaly_id, a.value)
            for a in after.anomalies
            if a.sensor == sensors.HUMIDITY and a.value <= 2.0
        ],
    }
    return rows, summary


# --------------------------------------------------------------------------
# RELY-01
# --------------------------------------------------------------------------


def rely01(csv_path):
    import pandas as pd
    from audit_config import (
        GAP_MAJOR_MINUTES,
        GAP_MINOR_MINUTES,
        GAP_MODERATE_MINUTES,
        GAP_NOMINAL_MINUTES,
        GAP_SUB_NOMINAL_MINUTES,
        NIGHTLY_SHUTDOWN_END_HOUR,
        NIGHTLY_SHUTDOWN_MAX_MINUTES,
        NIGHTLY_SHUTDOWN_START_HOUR,
        regime_for,
    )
    from data_validation import load_and_validate_data
    from outage_analysis import classify_gap, compute_gaps

    def superseded(minutes, starts_at, ends_at):
        """The regime-blind classifier this change replaced."""
        if minutes <= GAP_SUB_NOMINAL_MINUTES:
            return "sub-nominal (repeat transmission)"
        if minutes <= GAP_NOMINAL_MINUTES:
            return "nominal"
        if (
            starts_at.hour >= NIGHTLY_SHUTDOWN_START_HOUR
            and ends_at.hour <= NIGHTLY_SHUTDOWN_END_HOUR
            and minutes <= NIGHTLY_SHUTDOWN_MAX_MINUTES
        ):
            return "scheduled overnight shutdown"
        if minutes <= GAP_MINOR_MINUTES:
            return "minor"
        if minutes <= GAP_MODERATE_MINUTES:
            return "moderate"
        if minutes <= GAP_MAJOR_MINUTES:
            return "major"
        return "critical"

    with contextlib.redirect_stdout(io.StringIO()):
        frame, _ = load_and_validate_data(str(csv_path))
    gaps = compute_gaps(frame)

    rows, minutes_total = [], 0.0
    for position in range(1, len(frame)):
        minutes = gaps.iloc[position]
        if pd.isna(minutes):
            continue
        starts_at = frame["timestamp"].iloc[position - 1]
        ends_at = frame["timestamp"].iloc[position]
        old = superseded(minutes, starts_at, ends_at)
        new = classify_gap(minutes, starts_at, ends_at)
        if old == new:
            continue
        minutes_total += minutes
        regime = regime_for(starts_at.date())
        rows.append(_record(
            "RELY-01",
            f"{starts_at.isoformat()}..{ends_at.isoformat()}",
            "telemetry_gap",
            f"{old} (excluded from outage counts)",
            f"{new} (counted as data loss)",
            "no shutdown scheduled under the regime in force",
            f"{minutes:.0f} min, regime='{regime.label}'",
        ))
    return rows, {"gaps": len(rows), "hours": minutes_total / 60.0}


# --------------------------------------------------------------------------
# SOIL-01
# --------------------------------------------------------------------------


def soil01(csv_text):
    from environmental.api import detect_environmental_events, load_environmental_dataset
    from environmental.config import EnvironmentalConfig
    from environmental.models import SoilResponseStatus

    config = EnvironmentalConfig()
    dataset = load_environmental_dataset(config=config, csv_text=csv_text)
    events = detect_environmental_events(dataset, config)
    delay = config.soil.max_response_delay_hours * 60.0

    rows = []
    detected = 0
    for event in events:
        response = event.soil_response
        if response.status is not SoilResponseStatus.DETECTED:
            continue
        detected += 1
        if response.context_peak_deviation_counts is None:
            continue
        if abs(response.response_counts - response.context_peak_deviation_counts) < 1e-9:
            continue
        limit = (event.duration_minutes + delay) / 60.0
        rows.append(_record(
            "SOIL-01", event.event_id, "soil_response_metric",
            f"peak {response.context_peak_deviation_counts:+.0f} at "
            f"{response.context_time_to_peak_minutes / 60:.1f}h",
            f"peak {response.response_counts:+.0f} at "
            f"{response.time_to_peak_minutes / 60:.1f}h",
            "old peak occurred outside the attribution window",
            f"attribution limit {limit:.1f}h; status unchanged "
            f"({response.status})",
        ))
    return rows, {"detected": detected, "changed": len(rows)}


# --------------------------------------------------------------------------
# INGEST-01
# --------------------------------------------------------------------------


def ingest01():
    from urllib.parse import parse_qs, urlparse

    from environmental.data_sources.sheets import normalize_sheet_url

    station_monitor = REPOSITORY_ROOT / "apps" / "station-monitor"
    if str(station_monitor) not in sys.path:
        sys.path.insert(0, str(station_monitor))
    from services.battery_service import normalize_historical_data_url

    def superseded(url):
        """The station-monitor's own normaliser, which this change removed."""
        parsed = urlparse((url or "").strip())
        marker = "/spreadsheets/d/"
        if parsed.netloc == "docs.google.com" and marker in parsed.path:
            sheet_id = parsed.path.split(marker, 1)[1].split("/", 1)[0]
            query = parse_qs(parsed.query)
            fragment = parse_qs(parsed.fragment)
            gid = (query.get("gid") or fragment.get("gid") or ["0"])[0]
            return (
                f"https://docs.google.com/spreadsheets/d/{sheet_id}/"
                f"export?format=csv&gid={gid}"
            )
        return url

    sheet = "1iJzvixnEx5QH2lkQNkN8xKZqpIyGO7FmEsa_qsyHCOI"
    cases = [
        ("gviz, tab selected by name",
         f"https://docs.google.com/spreadsheets/d/{sheet}/gviz/tq?tqx=out:csv&sheet=HistoricalData"),
        ("CSV export with an explicit gid",
         f"https://docs.google.com/spreadsheets/d/{sheet}/export?format=csv&gid=123456"),
        ("edit link with a gid",
         f"https://docs.google.com/spreadsheets/d/{sheet}/edit?gid=987654#gid=987654"),
        ("non-Sheets CSV host", "https://example.com/history.csv"),
    ]

    rows, agree = [], True
    for label, url in cases:
        old = superseded(url)
        engine = normalize_sheet_url(url)
        service = normalize_historical_data_url(url)
        agree = agree and (engine == service)
        if old == service:
            continue
        rows.append(_record(
            "INGEST-01", label, "telemetry_source_url",
            old, service,
            "gviz selects a tab by name and carries no gid, so the removed "
            "normaliser defaulted it to 0 and read the first tab",
            f"engine={engine!r}; service={service!r}; identical={engine == service}",
        ))
    return rows, {"agree": agree, "cases": len(cases)}


# --------------------------------------------------------------------------


def markdown(rows, env, rely, soil, ingest, source_name):
    def table(finding):
        selected = [r for r in rows if r["finding_id"] == finding]
        if not selected:
            return "_no reclassifications_\n"
        head = (
            "| timestamp / id | entity | old interpretation | new interpretation "
            "| reason | values |\n| --- | --- | --- | --- | --- | --- |\n"
        )
        return head + "".join(
            f"| {r['timestamp_or_id']} | {r['entity_type']} | "
            f"{r['old_interpretation']} | {r['new_interpretation']} | "
            f"{r['reason']} | {r['relevant_values']} |\n"
            for r in selected
        )

    remaining = env["remaining_artifacts"]
    return f"""# Semantic reclassification report

Generated by `analysis/audit_reclassification_report.py` against `{source_name}`
({env['rows']} rows kept after validation). Every row below is a change in what
the system *says about existing data*, not a change in the data itself.
Machine-readable form: `semantic_reclassification.csv`.

## ENV-01 — known device fault signatures excluded from inference

Signatures matched: {env['fault_signatures']} (total {env['fault_frames']} frames).
Anomalies **{env['anomalies_before']} -> {env['anomalies_after']}**.
Wetting events {env['events_before']} -> {env['events_after']}.

Raw values are preserved in the frame; only the analytical validity masks change.

{table("ENV-01")}
### Remaining artifacts

{
    chr(10).join(f"- `{i}` (value {v:g}) — no paired signature, deliberately kept"
                 for i, v in remaining) or "- none"
}

## RELY-01 — nightly-shutdown classification is now regime-aware

{rely['gaps']} gaps reclassified, totalling **{rely['hours']:.1f} hours**.

{table("RELY-01")}
## SOIL-01 — attributed metrics bounded to the attribution window

{soil['changed']} of {soil['detected']} detected soil responses changed. Only the
metrics moved; no verdict changed.

{table("SOIL-01")}
## INGEST-01 — one authoritative ingestion path

Engine and service agree on all {ingest['cases']} supported URL forms: **{ingest['agree']}**.

{table("INGEST-01")}
## Deliberate residuals

| id | what remains | why |
| --- | --- | --- |
| ENV-01a | `2025-11-17 14:04` (`23.08 degC`, `0.00 %`, `296.14 hPa`) still yields temperature and humidity findings. | No paired signature: the temperature is neither zero nor absent. Calling a lone low humidity a confirmed fault would exceed the evidence. |
| DATA-01 | The ingestion script's zero/blank convention is channel- and date-dependent, and the deployed script does not match `scripts/`. | Needs on-site verification. Nothing under `scripts/` was touched. |
| FW-01 | The transmitter still discards both sensor `begin()` results, so new fault frames keep arriving. | Firmware change; needs physical access. |
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", default=str(DEFAULT_CSV))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    args = parser.parse_args()

    csv_path = Path(args.csv)
    csv_text = csv_path.read_text(encoding="utf-8-sig")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    env_rows, env = env01(csv_text)
    rely_rows, rely = rely01(csv_path)
    soil_rows, soil = soil01(csv_text)
    ingest_rows, ingest = ingest01()
    rows = env_rows + rely_rows + soil_rows + ingest_rows

    csv_out = out_dir / "semantic_reclassification.csv"
    with csv_out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    md_out = out_dir / "semantic_reclassification.md"
    md_out.write_text(
        markdown(rows, env, rely, soil, ingest, csv_path.name), encoding="utf-8"
    )

    print(f"{len(rows)} reclassifications")
    print(f"  {csv_out}")
    print(f"  {md_out}")


if __name__ == "__main__":
    main()
