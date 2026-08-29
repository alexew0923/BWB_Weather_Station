"""CLI Incident Explorer for the Better With Bees reliability audit."""

import argparse
import contextlib
import io
import json
import os
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from audit_config import STATION_TIMEZONE
from data_validation import load_and_validate_data
from incident_analysis import analyze_incident, parse_incident_timestamp, select_detected_outage


def build_parser():
    parser = argparse.ArgumentParser(
        description="Investigate one telemetry incident using the audit's validated data and schedule."
    )
    parser.add_argument("csv_path", help="HistoricalData CSV export")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--start", help="Incident start (Halifax local time unless offset supplied)")
    mode.add_argument("--outage", type=int, metavar="N", help="1-based real-outage index")
    parser.add_argument("--end", help="Incident end; required with --start")
    parser.add_argument("--before-hours", type=float, default=12.0)
    parser.add_argument("--after-hours", type=float, default=12.0)
    parser.add_argument("--output-dir", default="incident_output")
    return parser


def _slug(start, end, outage_number=None):
    if outage_number is not None:
        return f"outage_{outage_number:04d}"
    compact = lambda value: re.sub(r"[^0-9]", "", value.strftime("%Y%m%dT%H%M%S%z"))
    return f"{compact(start)}_{compact(end)}"


def _fmt_hours(hours):
    if hours is None:
        return "unavailable"
    minutes = int(round(hours * 60))
    return f"{minutes // 60}h {minutes % 60:02d}m"


def _fmt_battery(summary):
    if summary["trend_volts_per_hour"] is None:
        if summary["first_volts"] is not None:
            return f"{summary['first_volts']:.3f} V ({summary['status']})"
        return summary["status"]
    return (
        f"{summary['first_volts']:.3f} V -> {summary['last_volts']:.3f} V over "
        f"{summary['span_hours']:.2f} h ({summary['trend_volts_per_hour']:+.4f} V/h)"
    )


def _sensor_findings(summary):
    findings = []
    for sensor, values in summary.items():
        if values["status"] != "available":
            findings.append(f"{sensor}: {values['status']}")
        elif values["implausible_values"]:
            findings.append(f"{sensor}: {values['implausible_values']} implausible value(s)")
        elif values["completeness"] is not None and values["completeness"] < 1.0:
            findings.append(f"{sensor}: {100 * values['completeness']:.1f}% populated")
    return "; ".join(findings) if findings else "all commissioned fields populated; no implausible values"


def print_report(report):
    incident = report["incident"]
    pre = report["pre_window"]
    post = report["post_window"]
    print("\nINCIDENT")
    print(f"  Start / end           : {incident['start']} -> {incident['end']}")
    print(f"  Duration              : {_fmt_hours(incident['duration_hours'])}")
    print(f"  Schedulable time      : {_fmt_hours(incident['schedulable_hours'])}")
    print(f"  Scheduled inactive    : {_fmt_hours(incident['scheduled_inactive_hours'])}")
    print(f"  Expected / received   : {incident['expected_readings']} / {incident['received_readings']}")
    print(f"  Repeats / long gaps   : {incident['repeat_transmissions']} / {incident['irregular_gaps']}")
    if incident["received_readings"] == 0:
        observation = "no telemetry received"
    elif incident["over_baseline"]:
        observation = "over-baseline activity"
    elif incident["received_readings"] < incident["expected_readings"]:
        observation = "partial telemetry received"
    else:
        observation = "telemetry present at or above scheduled count"
    print(f"  Observation           : {observation}")
    print(f"  Sensor evidence       : {_sensor_findings(incident['sensors'])}")

    print("\nPRE-INCIDENT CONTEXT")
    print(f"  Last reading          : {pre['last_reading_before_incident'] or 'none in dataset'}")
    print(f"  Typical inter-arrival : {pre['typical_interarrival_minutes'] or 'unavailable'} min")
    print(f"  Minor / significant   : {pre['minor_gaps']} / {pre['significant_gaps']} gaps")
    print(f"  Repeats / long gaps   : {pre['repeat_transmissions']} / {pre['irregular_gaps']}")
    completeness = pre["telemetry_completeness"]
    print(f"  Telemetry completeness: {100 * completeness:.1f}%" if completeness is not None else "  Telemetry completeness: no scheduled opportunities")
    print(f"  Battery               : {_fmt_battery(pre['battery'])}")
    print(f"  Sensor evidence       : {_sensor_findings(pre['sensors'])}")

    print("\nRECOVERY")
    print(f"  First reading         : {post['first_reading_after_incident'] or 'none within selected post-window'}")
    print(f"  Recovery delay        : {_fmt_hours(post['recovery_delay_hours'])}")
    print(f"  Initial inter-arrival : {post['typical_interarrival_minutes'] or 'unavailable'} min")
    print(f"  Repeats / long gaps   : {post['repeat_transmissions']} / {post['irregular_gaps']}")
    print(f"  Battery               : {_fmt_battery(post['battery'])}")
    print(f"  Sensor evidence       : {_sensor_findings(post['sensors'])}")

    print("\nINTERPRETATION")
    for label in ("observed", "suggestive", "not_determinable"):
        print(f"  {label.replace('_', ' ').title()}:")
        for item in report["interpretation"][label]:
            print(f"    - {item}")


def plot_incident(df, report, path):
    incident = report["incident"]
    start = pd.Timestamp(incident["start"])
    end = pd.Timestamp(incident["end"])
    context_start = pd.Timestamp(report["pre_window"]["start"])
    context_end = pd.Timestamp(report["post_window"]["end"])
    rows = df[(df["timestamp"] >= context_start) & (df["timestamp"] <= context_end)]

    fig, ax = plt.subplots(figsize=(13, 4.8))
    ax.eventplot(mdates.date2num(rows["timestamp"]), lineoffsets=1, linelengths=0.55,
                 linewidths=0.8, colors="#263238", label="telemetry arrival")
    ax.axvspan(start, end, color="#c62828", alpha=0.18, label="incident interval")
    ax.axvline(start, color="#c62828", linewidth=1)
    ax.axvline(end, color="#c62828", linewidth=1)
    ax.set_ylim(0.55, 1.45)
    ax.set_yticks([])
    ax.set_xlabel(f"time ({STATION_TIMEZONE.key})")
    ax.set_title("Incident telemetry timeline\nEach vertical mark is one row received by the historical dataset")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d\n%H:%M", tz=STATION_TIMEZONE))
    ax.legend(loc="upper right", fontsize=8)

    battery = rows.dropna(subset=["Battery Voltage"])
    if not battery.empty:
        battery_ax = ax.twinx()
        battery_ax.plot(battery["timestamp"], battery["Battery Voltage"] / 1000.0,
                        color="#fb8c00", linewidth=1.2, marker=".", markersize=2.5,
                        label="battery voltage")
        battery_ax.set_ylabel("battery (V)", color="#e65100")
        battery_ax.tick_params(axis="y", labelcolor="#e65100")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def run(args):
    if (args.start is None) != (args.end is None):
        raise ValueError("--start and --end must be supplied together")
    if args.before_hours < 0 or args.after_hours < 0:
        raise ValueError("--before-hours and --after-hours must be non-negative")

    validation_log = io.StringIO()
    with contextlib.redirect_stdout(validation_log):
        df, validation = load_and_validate_data(args.csv_path)
    outage_meta = None
    outage_count = None
    if args.outage is not None:
        start, end, outage_meta, outage_count = select_detected_outage(df, args.outage)
    else:
        start = parse_incident_timestamp(args.start)
        end = parse_incident_timestamp(args.end)

    report = analyze_incident(
        df, start, end, args.before_hours, args.after_hours, source=args.csv_path,
        detected_outage=outage_meta,
    )
    if outage_meta is not None:
        report["selection"] = {
            "mode": "detected_outage",
            "outage_number": args.outage,
            "real_outage_count": outage_count,
            "severity": outage_meta["severity"],
            "missed_transmissions": int(outage_meta["missed_transmissions"]),
            "boundary_note": "The selected gap is bounded by received rows; those boundary rows are excluded from incident counts.",
        }
    else:
        report["selection"] = {"mode": "explicit_interval"}
    report["dataset_validation"] = {
        "rows_read": int(validation["rows_read"]),
        "rows_after": int(validation["rows_after"]),
        "rows_dropped": int(validation["rows_dropped"]),
        "backward_timestamp_jumps": int(validation["backward_jumps"]),
    }

    os.makedirs(args.output_dir, exist_ok=True)
    slug = _slug(start, end, args.outage)
    json_path = os.path.join(args.output_dir, f"incident_summary_{slug}.json")
    plot_path = os.path.join(args.output_dir, f"incident_plot_{slug}.png")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    plot_incident(df, report, plot_path)
    print(
        f"Validated {validation['rows_after']} telemetry rows "
        f"({validation['rows_dropped']} exact duplicate(s) dropped)."
    )
    print_report(report)
    print("\nOUTPUTS")
    print(f"  {json_path}")
    print(f"  {plot_path}")
    return report, json_path, plot_path


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        run(args)
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
