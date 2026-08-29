#!/usr/bin/env python3
"""Check whether fresh Better With Bees telemetry is reaching Google Sheets."""

import argparse
import csv
import io
import json
import os
import smtplib
import sys
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from zoneinfo import ZoneInfo


CSV_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1iJzvixnEx5QH2lkQNkN8xKZqpIyGO7FmEsa_qsyHCOI/export?format=csv&gid=0"
)
HEALTHY_THRESHOLD_MINUTES = 10
OFFLINE_THRESHOLD_MINUTES = 30

HALIFAX = ZoneInfo("America/Halifax")
TIMESTAMP_COLUMN = "Timestamp"
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
STATE_FILE = Path(__file__).with_name("state.json")


class NotificationError(Exception):
    """An expected email configuration or delivery error."""


def download_csv():
    """Download the public Google Sheet and return its text."""
    with urlopen(CSV_URL, timeout=15) as response:
        return response.read().decode("utf-8-sig")


def newest_timestamp(csv_text):
    """Return the newest valid timestamp found anywhere in the CSV."""
    rows = csv.DictReader(io.StringIO(csv_text))
    if not rows.fieldnames or TIMESTAMP_COLUMN not in rows.fieldnames:
        raise ValueError(f"CSV has no '{TIMESTAMP_COLUMN}' column")

    newest = None
    for row in rows:
        value = (row.get(TIMESTAMP_COLUMN) or "").strip()
        try:
            timestamp = datetime.strptime(value, TIMESTAMP_FORMAT).replace(tzinfo=HALIFAX)
        except ValueError:
            continue
        if newest is None or timestamp > newest:
            newest = timestamp

    if newest is None:
        raise ValueError("the Google Sheet contains no valid telemetry timestamps")
    return newest


def status_for(age_minutes):
    if age_minutes <= HEALTHY_THRESHOLD_MINUTES:
        return "HEALTHY"
    if age_minutes < OFFLINE_THRESHOLD_MINUTES:
        return "DELAYED"
    return "OFFLINE"


def format_age(seconds):
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


def load_state(path=STATE_FILE):
    try:
        with path.open(encoding="utf-8") as file:
            state = json.load(file)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read state file: {error}") from error
    if not isinstance(state, dict):
        raise ValueError("state file must contain a JSON object")
    return state


def save_state(state, path=STATE_FILE):
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    except OSError as error:
        raise ValueError(f"could not save state file: {error}") from error


def alert_message(status, latest, age_seconds):
    timestamp = latest.strftime("%Y-%m-%d %H:%M:%S %Z")
    if status == "HEALTHY":
        detail = f"Telemetry has resumed.\n\nLatest telemetry:\n{timestamp}"
    elif status == "DELAYED":
        detail = (
            f"Telemetry is delayed. The newest data reached Google Sheets "
            f"{format_age(age_seconds)} ago.\n\nLast telemetry:\n{timestamp}"
        )
    else:
        detail = (
            f"Fresh telemetry has not reached Google Sheets for "
            f"{format_age(age_seconds)}.\n\nLast telemetry:\n{timestamp}"
        )
    return (
        f"Better With Bees — StationWatch\n\nStatus: {status}\n\n{detail}\n\n"
        "This reports the Google Sheets observation point; it does not identify "
        "which upstream component may have failed."
    )


def send_email(status, latest, age_seconds):
    names = [
        "STATIONWATCH_SMTP_HOST",
        "STATIONWATCH_SMTP_USER",
        "STATIONWATCH_SMTP_PASSWORD",
        "STATIONWATCH_EMAIL_TO",
    ]
    settings = {name: os.environ.get(name) for name in names}
    missing = [name for name, value in settings.items() if not value]
    if missing:
        raise NotificationError("missing email setting(s): " + ", ".join(missing))

    try:
        port = int(os.environ.get("STATIONWATCH_SMTP_PORT", "587"))
    except ValueError as error:
        raise NotificationError("STATIONWATCH_SMTP_PORT must be a number") from error

    message = EmailMessage()
    message["Subject"] = f"StationWatch: {status}"
    message["From"] = os.environ.get("STATIONWATCH_EMAIL_FROM", settings["STATIONWATCH_SMTP_USER"])
    message["To"] = settings["STATIONWATCH_EMAIL_TO"]
    message.set_content(alert_message(status, latest, age_seconds))

    try:
        with smtplib.SMTP(settings["STATIONWATCH_SMTP_HOST"], port, timeout=15) as smtp:
            smtp.starttls()
            smtp.login(settings["STATIONWATCH_SMTP_USER"], settings["STATIONWATCH_SMTP_PASSWORD"])
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as error:
        raise NotificationError(f"could not send email: {error}") from error


def record_result(status, latest, checked_at, age_seconds, notifier=send_email, state_path=STATE_FILE):
    """Save the result and alert once when status changes."""
    previous = load_state(state_path)
    first_run = not previous
    last_alerted = previous.get("last_alerted_status")
    state = {
        "status": status,
        "last_telemetry_timestamp": latest.isoformat(),
        "last_checked": checked_at.isoformat(),
        "last_alerted_status": status if first_run else last_alerted,
    }
    save_state(state, state_path)

    if first_run or last_alerted == status:
        return False

    notifier(status, latest, age_seconds)
    state["last_alerted_status"] = status
    save_state(state, state_path)
    return True


def run_check():
    latest = newest_timestamp(download_csv())
    checked_at = datetime.now(HALIFAX)
    age_seconds = (checked_at - latest).total_seconds()
    if age_seconds < -60:
        raise ValueError("the newest telemetry timestamp is in the future")

    age_seconds = max(0, age_seconds)
    status = status_for(age_seconds / 60)
    print("Better With Bees — StationWatch Live")
    print(f"Status: {status}")
    print(f"Latest telemetry: {latest.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"Age: {format_age(age_seconds)} ago")

    alert_sent = record_result(status, latest, checked_at, age_seconds)
    print(f"Checked: {checked_at.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"Alert sent: {'yes' if alert_sent else 'no'}")
    if status == "OFFLINE":
        print("Fresh telemetry is not reaching Google Sheets; this does not prove the station failed.")


def test_alert(status):
    now = datetime.now(HALIFAX)
    age = 31 * 60 if status == "OFFLINE" else 2 * 60
    send_email(status, now - timedelta(seconds=age), age)
    print(f"Test {status} email sent. The Sheet and state file were not changed.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-alert", choices=("OFFLINE", "HEALTHY"))
    args = parser.parse_args(argv)
    try:
        if args.test_alert:
            test_alert(args.test_alert)
        else:
            run_check()
    except (HTTPError, URLError, TimeoutError) as error:
        print("MONITOR ERROR")
        print(f"Could not retrieve Google Sheets data: {error}")
        print("Station status could not be determined.")
        return 1
    except (UnicodeError, csv.Error, ValueError) as error:
        print("MONITOR ERROR")
        print(f"Station status could not be determined: {error}")
        return 1
    except NotificationError as error:
        print(f"ALERT ERROR: {error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
