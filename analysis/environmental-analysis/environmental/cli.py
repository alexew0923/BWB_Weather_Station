"""Development and validation CLI.

    python -m environmental.cli profile
    python -m environmental.cli events --limit 20
    python -m environmental.cli event wetting-2026-05-14T08:15-0300
    python -m environmental.cli summary --json
    python -m environmental.cli baseline
    python -m environmental.cli current
    python -m environmental.cli validate
    python -m environmental.cli plots --output-dir output

Every command reads the production remote source unless ``--csv`` is given.
``--csv`` is a development override and is labelled as one in the output.

The CLI is for people. A frontend imports the Python API rather than parsing
this output; ``--json`` exists for scripting and for eyeballing the exact
structure a service would receive, not as an integration boundary.
"""

import argparse
import json
import sys

from . import sensors
from .api import (
    analyze_environment,
    detect_environmental_events,
    get_current_environmental_state,
    get_environmental_baseline,
    get_event,
    list_events,
    load_environmental_dataset,
    profile_environment,
    telemetry_profile,
)
from .config import EnvironmentalConfig
from .data_sources import SourceReference, read_local_csv_text
from .errors import EnvironmentalAnalysisError
from .models import to_serialisable
from .version import version_metadata


def _load(arguments, config):
    if arguments.csv:
        source = SourceReference(
            kind="local",
            label="Local development CSV (explicit --csv override)",
            setting="--csv",
            path=arguments.csv,
        )
        return load_environmental_dataset(
            config=config, source=source, csv_text=read_local_csv_text(arguments.csv)
        )
    return load_environmental_dataset(config=config, allow_local_override=False)


def _emit(payload, as_json):
    if as_json:
        print(json.dumps(to_serialisable(payload), indent=2, sort_keys=False))
        return
    print(payload)


def _rule(title):
    return f"{title}\n{'-' * len(title)}"


def command_profile(arguments, config):
    dataset = _load(arguments, config)
    profiles = profile_environment(dataset)
    if arguments.json:
        _emit(
            {"telemetry": telemetry_profile(dataset), "sensors": profiles}, True
        )
        return 0

    print(_rule("Environmental sensor profile"))
    print(f"Source            : {dataset.source.describe()}")
    print(f"Rows              : {len(dataset)}")
    print(f"Period            : {dataset.start_time} .. {dataset.end_time}")
    coverage = dataset.coverage()["fraction"]
    print(f"Telemetry coverage: {coverage:.1%}" if coverage else "Telemetry coverage: unknown")
    print()
    for name, profile in profiles.items():
        statistics = profile.statistics
        print(f"{name}  [{profile.unit}]"
              f"{'' if profile.calibrated else '  (uncalibrated)'}")
        print(f"    valid           : {profile.valid_observations} "
              f"({profile.valid_fraction:.1%} of received rows)")
        if statistics.valid_count:
            print(f"    min/median/max  : {statistics.minimum:.2f} / "
                  f"{statistics.median:.2f} / {statistics.maximum:.2f}")
            print(f"    p10/p90         : {statistics.p10:.2f} / {statistics.p90:.2f}")
        for key in ("dry_fraction", "dry_state_zero_step_fraction",
                    "zero_fraction_of_present", "diurnal_swing_counts",
                    "spearman_with_temperature", "impossible_readings"):
            if key in profile.findings and profile.findings[key] is not None:
                print(f"    {key:<16}: {profile.findings[key]}")
        print()
    return 0


def command_events(arguments, config):
    dataset = _load(arguments, config)
    events = detect_environmental_events(dataset, config)
    selected = list_events(
        events,
        classification=arguments.classification,
        soil_status=arguments.soil_status,
        limit=arguments.limit,
    )
    if arguments.json:
        _emit(selected, True)
        return 0

    print(_rule(f"Detected wetting events ({len(events)} total, "
                f"{len(selected)} shown)"))
    print(f"{'event id':<34} {'dur/min':>8} {'peak':>7} "
          f"{'classification':<26} {'soil':<13} quality")
    for event in selected:
        peak = event.observations.peak_deviation_counts
        print(
            f"{event.event_id:<34} {event.duration_minutes:8.0f} "
            f"{(peak if peak is not None else float('nan')):7.0f} "
            f"{str(event.classification):<26} "
            f"{str(event.soil_response.status):<13} {event.data_quality.level}"
        )
    return 0


def command_event(arguments, config):
    dataset = _load(arguments, config)
    events = detect_environmental_events(dataset, config)
    event = get_event(events, arguments.event_id)
    if arguments.json:
        _emit(event, True)
        return 0

    print(_rule(event.event_id))
    print(f"Type           : {event.event_type}")
    print(f"Window         : {event.start_time} .. {event.end_time}")
    print(f"Duration       : {event.duration_minutes:.0f} minutes")
    print(f"Classification : {event.classification}")
    print(f"Evidence       : {event.evidence_strength}")
    print(f"Data quality   : {event.data_quality.level}")
    print(f"Detector       : {event.detection_method} v{event.detection_version}")
    print()
    print("Observations")
    for key, value in to_serialisable(event.observations).items():
        print(f"    {key:<38} {value}")
    print()
    print("Evidence")
    for item in event.evidence:
        print(f"    [{item.kind}] {item.statement}")
    print()
    print("Soil response")
    print(f"    status                : {event.soil_response.status}")
    print(f"    direction             : {event.soil_response.direction}")
    print(f"    quality               : {event.soil_response.quality.level}")
    for reason in event.soil_response.quality.reasons:
        print(f"        - {reason}")
    for item in event.soil_response.evidence:
        print(f"    [{item.kind}] {item.statement}")
    print()
    print("Interpretation")
    print(f"    {event.interpretation.statement}")
    print(f"    cause: {event.interpretation.cause}")
    for caveat in event.interpretation.caveats:
        print(f"    caveat: {caveat}")
    for warning in event.warnings:
        print(f"    warning: {warning}")
    return 0


def command_summary(arguments, config):
    dataset = _load(arguments, config)
    summary = analyze_environment(dataset, config)
    if arguments.json:
        _emit(summary, True)
        return 0

    print(_rule("Environmental summary"))
    print(f"Station        : {summary.station_id}")
    print(f"Source         : {summary.source.get('label')}")
    print(f"Period         : {dataset.start_time} .. {dataset.end_time}")
    print(f"Rows           : {len(dataset)}")
    print(f"Overall quality: {summary.quality.level}")
    print()
    print("Events by classification")
    for key, value in summary.event_counts.items():
        print(f"    {key:<28} {value}")
    print()
    print("Soil response")
    for key, value in summary.soil_response_counts.items():
        print(f"    {key:<28} {value}")
    print()
    print(f"Anomalies      : {len(summary.anomalies)}")
    print()
    print("Limitations")
    for item in summary.limitations:
        print(f"    - {item}")
    return 0


def command_baseline(arguments, config):
    dataset = _load(arguments, config)
    events = detect_environmental_events(dataset, config)
    baseline = get_environmental_baseline(dataset, events, config)
    if arguments.json:
        _emit(baseline, True)
        return 0

    print(_rule("Environmental baseline"))
    print(f"{'period':<10} {'rows':>6} {'coverage':>9} {'evidence':<12} "
          f"{'temp med':>9} {'rh med':>8} {'events':>7}")
    for period in baseline.periods:
        temperature = period.sensors.get(sensors.TEMPERATURE)
        humidity = period.sensors.get(sensors.HUMIDITY)
        coverage = (
            f"{period.telemetry_coverage:.0%}"
            if period.telemetry_coverage is not None
            else "  n/a"
        )
        print(
            f"{period.period:<10} {period.observations:6d} {coverage:>9} "
            f"{str(period.evidence_strength):<12} "
            f"{(temperature.median if temperature else float('nan')):9.1f} "
            f"{(humidity.median if humidity else float('nan')):8.1f} "
            f"{period.wetness_event_count:7d}"
        )
    return 0


def command_current(arguments, config):
    state = get_current_environmental_state(config=config)
    if arguments.json:
        _emit(state, True)
        return 0
    print(_rule("Current environmental state"))
    print(f"Freshness : {state.freshness}")
    print(f"Source    : {state.source_label}")
    print(f"As of     : {state.as_of}")
    print(f"Latest    : {state.latest_observation_at}")
    print(f"Summary   : {state.summary}")
    for name, reading in state.readings.items():
        print(f"    {name:<20} {reading.value} {reading.unit}")
    if state.quality and state.quality.reasons:
        for reason in state.quality.reasons:
            print(f"    note: {reason}")
    return 0


def command_validate(arguments, config):
    """Run the whole pipeline and print a validation report."""
    dataset = _load(arguments, config)
    summary = analyze_environment(dataset, config)
    payload = {
        "dataset": dataset.describe(),
        "event_counts": summary.event_counts,
        "soil_response_counts": summary.soil_response_counts,
        "anomaly_count": len(summary.anomalies),
        "versions": version_metadata(),
    }
    if arguments.json:
        _emit(payload, True)
        return 0
    print(json.dumps(to_serialisable(payload), indent=2))
    return 0


def command_plots(arguments, config):
    from . import plots
    from .soil import build_soil_context

    dataset = _load(arguments, config)
    events = detect_environmental_events(dataset, config)
    context = build_soil_context(dataset, config.soil)
    written = [
        plots.plot_wetness_with_events(dataset, events, arguments.output_dir),
        plots.plot_event_duration_distribution(events, arguments.output_dir),
        plots.plot_soil_response_vs_event(events, arguments.output_dir),
    ]
    ranked = sorted(
        events,
        key=lambda event: event.observations.peak_deviation_counts or 0.0,
        reverse=True,
    )
    for event in ranked[: arguments.event_plots]:
        written.append(plots.plot_event_context(dataset, event, arguments.output_dir))
        written.append(
            plots.plot_event_soil_response(
                dataset, event, arguments.output_dir, context
            )
        )
        if event.post_event_dynamics is not None:
            written.append(
                plots.plot_post_event_trajectory(
                    dataset, event, arguments.output_dir, context
                )
            )
    for path in written:
        print(path)
    return 0


COMMANDS = {
    "profile": command_profile,
    "events": command_events,
    "event": command_event,
    "summary": command_summary,
    "baseline": command_baseline,
    "current": command_current,
    "validate": command_validate,
    "plots": command_plots,
}


def build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m environmental.cli",
        description=(
            "Better With Bees environmental analysis engine. Reads the remote "
            "historical Google Sheet configured by HISTORICAL_DATA_URL unless "
            "--csv is given."
        ),
    )
    parser.add_argument(
        "--csv",
        help=(
            "development override: analyse a local CSV instead of the remote "
            "production source"
        ),
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("profile", help="sensor profiling")

    events = subparsers.add_parser("events", help="list detected events")
    events.add_argument("--limit", type=int)
    events.add_argument("--classification")
    events.add_argument("--soil-status", dest="soil_status")

    event = subparsers.add_parser("event", help="one event in detail")
    event.add_argument("event_id")

    subparsers.add_parser("summary", help="full analysis summary")
    subparsers.add_parser("baseline", help="environmental baselines")
    subparsers.add_parser("current", help="current/recent environmental state")
    subparsers.add_parser("validate", help="pipeline validation report")

    plots = subparsers.add_parser("plots", help="write diagnostic plots")
    plots.add_argument("--output-dir", default="output")
    plots.add_argument("--event-plots", type=int, default=3)
    return parser


def main(argv=None):
    parser = build_parser()
    arguments = parser.parse_args(argv)
    for attribute in ("limit", "classification", "soil_status"):
        if not hasattr(arguments, attribute):
            setattr(arguments, attribute, None)
    config = EnvironmentalConfig()
    try:
        return COMMANDS[arguments.command](arguments, config)
    except EnvironmentalAnalysisError as error:
        print(f"{error.summary}\nDetail: {error.detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
