#!/usr/bin/env python3
"""Check whether fresh Better With Bees telemetry is reaching Google Sheets."""

import csv
import io
import sys
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from zoneinfo import ZoneInfo


CSV_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1iJzvixnEx5QH2lkQNkN8xKZqpIyGO7FmEsa_qsyHCOI/export?format=csv&gid=0"
)
HEALTHY_AFTER_MINUTES = 10
OFFLINE_AFTER_MINUTES = 30

HALIFAX = ZoneInfo("America/Halifax")
TIMESTAMP_COLUMN = "Timestamp"
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def download_csv():
    """Download the public Google Sheet and return its text."""
    with urlopen(CSV_URL, timeout=15) as response:
        return response.read().decode("utf-8-sig")


def newest_timestamp(csv_text):
    """Return the newest valid timestamp found anywhere in the CSV."""
    rows = csv.DictReader(io.StringIO(csv_text))
    if not rows.fieldnames or TIMESTAMP_COLUMN not in rows.fieldnames:
        raise ValueError(f"CSV has no '{TIMESTAMP_COLUMN}' column")

    valid_timestamps = []
    for row in rows:
        value = (row.get(TIMESTAMP_COLUMN) or "").strip()
        try:
            timestamp = datetime.strptime(value, TIMESTAMP_FORMAT)
        except ValueError:
            continue
        valid_timestamps.append(timestamp.replace(tzinfo=HALIFAX))

    if not valid_timestamps:
        raise ValueError("the Google Sheet contains no valid telemetry timestamps")
    return max(valid_timestamps)


def format_age(seconds):
    """Format an age as a short, readable duration."""
    seconds = max(0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)

    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def status_for(age_minutes):
    if age_minutes <= HEALTHY_AFTER_MINUTES:
        return "HEALTHY"
    if age_minutes < OFFLINE_AFTER_MINUTES:
        return "DELAYED"
    return "OFFLINE"


def run():
    latest = newest_timestamp(download_csv())
    now = datetime.now(HALIFAX)
    age_seconds = (now - latest).total_seconds()
    if age_seconds < -60:
        raise ValueError("the newest telemetry timestamp is in the future")

    status = status_for(max(0, age_seconds) / 60)
    print("Better With Bees — StationWatch Live")
    print(f"Status: {status}")
    print(f"Latest telemetry: {latest.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"Age: {format_age(age_seconds)} ago")
    if status == "OFFLINE":
        print("Fresh telemetry has not reached Google Sheets; this does not prove the station failed.")


def main():
    try:
        run()
    except (HTTPError, URLError, TimeoutError) as error:
        print("Better With Bees — StationWatch Live")
        print(f"Error: could not download the Google Sheet CSV ({error})")
        return 1
    except (UnicodeError, csv.Error, ValueError) as error:
        print("Better With Bees — StationWatch Live")
        print(f"Error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
