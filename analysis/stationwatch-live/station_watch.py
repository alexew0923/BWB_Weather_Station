#!/usr/bin/env python3
"""Terminal view of whether fresh Better With Bees telemetry is reaching Sheets.

All health logic lives in ``station_health``; this file only renders it.

    python station_watch.py
"""

import sys

from station_health import (
    MonitorError,
    Status,
    StationMonitor,
    format_timestamp,
)


TITLE = "Better With Bees — StationWatch Live"
RULE = "─" * len(TITLE)


def render_report(report):
    """Return the terminal text for a successful check."""
    lines = [
        TITLE,
        RULE,
        f"Status:          {report.status}",
        f"Last telemetry:  {format_timestamp(report.latest_timestamp)}",
        f"Age:             {report.age_text}",
        f"Checked:         {format_timestamp(report.checked_at)}",
        "",
        report.summary,
    ]
    if report.status is not Status.HEALTHY:
        lines.append(
            "This reports the Google Sheets observation point only; it does not "
            "identify which upstream component failed."
        )
    return "\n".join(lines)


def render_monitor_error(error):
    """Return the terminal text for a monitoring failure, never an OFFLINE status."""
    return "\n".join(
        [
            TITLE,
            RULE,
            "Status:          MONITOR ERROR",
            "",
            error.summary,
            f"Detail: {error.detail}",
            "Current telemetry status cannot be determined.",
        ]
    )


def main():
    """Run one check and print it. Returns 0 on success, 1 on monitor error."""
    monitor = StationMonitor()
    try:
        print(render_report(monitor.check()))
    except MonitorError as error:
        print(render_monitor_error(error))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
