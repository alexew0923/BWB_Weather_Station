"""CSV/JSON artifacts and concise console reporting."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from sensor_rules import HISTORICAL_REGIMES, SENSOR_RULES, SEVERITY_RULES


def future_instrumentation_candidates(results):
    """Only candidates tied to ambiguity actually encountered by the analysis."""
    events = results["events"]
    candidates = []
    if not events.empty and events["event_type"].str.contains("missing").any():
        candidates.extend([
            "per-sensor read success/failure flags and I2C error state",
            "packet sequence number independent of reboot count",
        ])
    pressure = results["pressure_clusters"]
    if not pressure.empty:
        candidates.append("raw pressure read status plus sensor identity/address")
    if "Soil Moisture" in results["sensor_summary"]["sensor"].values:
        candidates.append("firmware version and explicit soil-sample-valid flag in each packet")
    if not events.empty and (events["event_type"] == "cross_sensor_missing_signature").any():
        candidates.extend(["device reset reason", "power-rail/brownout status at measurement time"])
    return list(dict.fromkeys(candidates))


def _json_value(value):
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and np.isnan(value):
        return None
    return value


def build_json_summary(results, source_path):
    events = results["events"]
    data = results["data"]
    event_counts = (
        events["event_type"].value_counts().sort_index().to_dict()
        if not events.empty else {}
    )
    severity_counts = (
        events["severity"].value_counts().sort_index().to_dict()
        if not events.empty else {}
    )
    return {
        "source": str(source_path),
        "period": {
            "start": data["timestamp"].min().isoformat(),
            "end": data["timestamp"].max().isoformat(),
            "received_rows_analyzed": len(data),
        },
        "question": "When telemetry was received, how trustworthy were individual sensor measurements?",
        "scope": "historical analysis only; no component-level root-cause claim",
        "sensor_summary": [
            {key: _json_value(value) for key, value in record.items()}
            for record in results["sensor_summary"].to_dict(orient="records")
        ],
        "event_counts": {key: int(value) for key, value in event_counts.items()},
        "severity_counts": {key: int(value) for key, value in severity_counts.items()},
        "rate_thresholds": {
            sensor: _json_value(value)
            for sensor, value in results["rate_thresholds"].items()
        },
        "interpretation": {
            "observed": "direct field population, values, and timestamped changes in received rows",
            "suggestive": "deterministic patterns consistent with invalid or abnormal telemetry, without root-cause assignment",
            "not_determinable": "sensor, wiring, I2C, firmware, electrical, radio, parsing, or ingestion origin generally cannot be separated from this CSV",
        },
        "soil_semantics": (
            "Older committed code sampled every sixth boot and emitted zero otherwise; later ingestion blanked zeros; current code samples every transmission. "
            "Opportunity-adjusted completeness uses those documented rules, but Git dates do not prove deployment dates."
        ),
        "rain_semantics": "raw analog wetness value, not precipitation quantity",
        "severity_rules": SEVERITY_RULES,
        "bounds": {
            sensor: {
                "unit": rule.unit,
                "impossible": [rule.impossible_low, rule.impossible_high],
                "suspicious": [rule.suspicious_low, rule.suspicious_high],
            }
            for sensor, rule in SENSOR_RULES.items()
        },
        "historical_regimes": [
            {
                "starts_on": None if regime.starts_on.year == 1 else regime.starts_on.isoformat(),
                "label": regime.label,
                "evidence_note": regime.evidence_note,
            }
            for regime in HISTORICAL_REGIMES
        ],
        "future_instrumentation_candidates": future_instrumentation_candidates(results),
    }


def write_outputs(results, output_dir, source_path):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    tables = {
        "sensor_summary.csv": results["sensor_summary"],
        "sensor_daily_metrics.csv": results["daily"],
        "sensor_regime_metrics.csv": results["regimes"],
        "anomaly_events.csv": results["events"],
        "pressure_anomaly_clusters.csv": results["pressure_clusters"],
    }
    for filename, table in tables.items():
        table.to_csv(output_dir / filename, index=False)
    with (output_dir / "analysis_summary.json").open("w", encoding="utf-8") as handle:
        json.dump(build_json_summary(results, source_path), handle, indent=2, allow_nan=False)


def print_console_summary(results, output_dir):
    summary = results["sensor_summary"]
    events = results["events"]
    data = results["data"]
    print("=" * 88)
    print("HISTORICAL SENSOR-HEALTH SUMMARY")
    print("=" * 88)
    print(f"Received rows analyzed : {len(data):,}")
    print(f"Period                 : {data['timestamp'].min()} to {data['timestamp'].max()}")
    print("Denominator            : received telemetry rows / documented sensor opportunities")
    print()
    print(f"{'Sensor':<18} {'Complete':>9} {'Raw field':>10} {'Missing':>8} {'Impossible':>11} {'Flatlines':>10} {'Changes':>8}")
    for _, row in summary.iterrows():
        complete = "n/a" if pd.isna(row["completeness"]) else f"{100 * row['completeness']:.1f}%"
        print(
            f"{row['sensor']:<18} {complete:>9} {100 * row['raw_field_completeness']:>9.1f}% "
            f"{int(row['missing_runs']):>8} {int(row['impossible_events']):>11} "
            f"{int(row['flatline_events']):>10} {int(row['large_change_events']):>8}"
        )
    print()
    print("Anomaly events by type:")
    if events.empty:
        print("  none")
    else:
        for event_type, count in events["event_type"].value_counts().sort_index().items():
            print(f"  {event_type:<36} {count:>6}")
    print()
    pressure = results["pressure_clusters"]
    print(f"Pressure anomaly clusters: {len(pressure)}")
    if not pressure.empty:
        impossible = pressure[pressure["cluster_type"] == "impossible"]
        approx_404 = data["Air Pressure"].between(4.035, 4.045).sum()
        print(f"  impossible clusters   : {len(impossible)}")
        print(f"  approximately 4.04 hPa: {int(approx_404)} readings")
    print()
    print("Interpretation:")
    print("  Observed        : field population, values, runs, and actual-time changes.")
    print("  Suggestive      : deterministic patterns can establish invalid telemetry, not its component cause.")
    print("  Not determinable: sensor vs wiring/I2C/firmware/power/parsing/ingestion origin.")
    print()
    print("Soil: opportunity-adjusted completeness uses documented periodic/every-row code eras;")
    print("      deployment dates remain unverified. Rain Value is raw wetness ADC, not rainfall.")
    print()
    print(f"Outputs written to: {Path(output_dir)}\n")
    print("Future instrumentation candidates:")
    for candidate in future_instrumentation_candidates(results):
        print(f"  - {candidate}")
