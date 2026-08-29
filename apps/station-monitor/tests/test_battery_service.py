"""Source-layer checks for remote historical telemetry."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from services import battery_service  # noqa: E402
from services.battery_service import (  # noqa: E402
    HistoricalDataError,
    fetch_historical_csv,
    normalize_historical_data_url,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self.payload


class HistoricalSourceTests(unittest.TestCase):
    def test_service_has_no_default_historical_data_setting(self):
        self.assertFalse(
            any(name.startswith("DEFAULT_HISTORICAL_DATA") for name in vars(battery_service))
        )

    def test_google_edit_url_becomes_csv_export(self):
        normalized = normalize_historical_data_url(
            "https://docs.google.com/spreadsheets/d/example_sheet/edit?gid=42#gid=42"
        )
        self.assertEqual(
            normalized,
            "https://docs.google.com/spreadsheets/d/example_sheet/export?format=csv&gid=42",
        )

    def test_fetch_decodes_csv_and_rejects_html(self):
        with patch(
            "services.battery_service.urlopen",
            return_value=FakeResponse(b"Timestamp,Battery Voltage\n2026-01-01 00:00:00,3900\n"),
        ):
            text = fetch_historical_csv("https://example.com/history.csv")
        self.assertIn("Battery Voltage", text)

        with patch(
            "services.battery_service.urlopen",
            return_value=FakeResponse(b"<!doctype html><title>Sign in</title>"),
        ):
            with self.assertRaises(HistoricalDataError):
                fetch_historical_csv("https://example.com/history.csv")

    def test_malformed_url_is_actionable(self):
        with self.assertRaises(HistoricalDataError) as raised:
            normalize_historical_data_url("not-a-url")
        self.assertIn("malformed", raised.exception.summary.lower())


if __name__ == "__main__":
    unittest.main()
