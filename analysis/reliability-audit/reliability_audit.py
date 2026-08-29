"""Reliability Audit for the BWB Weather Station.

Usage:
    python reliability_audit.py <path_to_csv> [output_dir]
"""

import os
import sys

# Keep the established function and constant names importable from this module
# while their implementations live beside their conceptual responsibilities.
from audit_config import *  # noqa: F403
from data_validation import (
    deduplicate_exact_repeats,
    detect_backward_timestamp_jumps,
    flag_corrupted_frame,
    load_and_validate_data,
    report_implausible_values,
    require_expected_columns,
    resolve_timestamp_column,
)
from outage_analysis import (
    classify_gap,
    compute_gaps,
    detect_outages,
    real_outages,
    significant_outages,
)
from reliability_metrics import (
    add_daily_gap_stats,
    add_slot_index,
    reconcile_transmissions,
    build_daily_reliability,
    classify_day,
    compute_daily_row_completeness,
    compute_sensor_completeness,
    find_commissioning_date,
    verify_baseline_regimes,
)
from reporting import (
    NULL_RATE_SWING_THRESHOLD,
    print_reconciliation,
    _commissioning_notes,
    _null_rate_swing_notes,
    build_anomaly_notes,
    print_baseline_check,
    print_summary,
    report_gap_distribution,
    summarise_daily_reliability,
    summarise_outages,
    summarise_sensor_completeness,
)
from visualization import (
    CLASS_COLOURS,
    _annotate_soil_moisture_anomaly,
    _shade_no_data_days,
    plot_daily_completeness,
    plot_daily_largest_gap,
    plot_gap_distribution,
    plot_sensor_completeness,
)


def main():
    if len(sys.argv) < 2:
        raise SystemExit(
            "Usage: python reliability_audit.py <path_to_csv> [output_dir]"
        )

    csv_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "audit_output"
    os.makedirs(output_dir, exist_ok=True)

    df, validation = load_and_validate_data(csv_path)
    # Position every row on the powered timeline once; the outage counts and the
    # reconciliation are both differences of these slot indices.
    df = add_slot_index(df)

    gaps = compute_gaps(df)
    report_gap_distribution(gaps)
    outages = detect_outages(df, gaps)
    summarise_outages(outages)

    daily = compute_daily_row_completeness(df)
    daily = add_daily_gap_stats(daily, outages)

    sensor_daily = compute_sensor_completeness(df)
    sensor_summary = summarise_sensor_completeness(df)

    daily_reliability = build_daily_reliability(daily, sensor_daily)
    summarise_daily_reliability(daily_reliability)

    reconciliation = reconcile_transmissions(df, daily)
    print_reconciliation(reconciliation)

    written = [
        _write_csv(outages, output_dir, "outage_intervals.csv"),
        _write_csv(sensor_daily, output_dir, "sensor_completeness.csv"),
        _write_csv(daily_reliability, output_dir, "daily_reliability.csv"),
        plot_daily_completeness(daily_reliability, output_dir),
        plot_daily_largest_gap(daily_reliability, output_dir),
        plot_sensor_completeness(sensor_daily, output_dir),
        plot_gap_distribution(gaps, output_dir),
    ]

    print_summary(df, validation, daily, daily_reliability, sensor_summary,
                  outages, reconciliation)

    print("=" * 78)
    print("OUTPUTS")
    print("=" * 78)
    for path in written:
        print(f"  {path}")
    print()


def _write_csv(frame, output_dir, filename):
    path = os.path.join(output_dir, filename)
    frame.to_csv(path, index=False)
    return path


if __name__ == "__main__":
    main()
