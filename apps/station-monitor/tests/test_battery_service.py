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
    resolve_historical_source,
    safe_historical_error_detail,
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

    def test_shared_source_selection_is_local_first_then_remote(self):
        local = resolve_historical_source(
            {
                "BWB_HISTORICAL_CSV": "/private/local/HistoricalData.csv",
                "HISTORICAL_DATA_URL": "https://example.com/history.csv",
            }
        )
        self.assertEqual(local["kind"], "local")
        self.assertEqual(local["display_label"], "Local historical data")
        self.assertIsNone(local["remote_url"])

        remote = resolve_historical_source(
            {"HISTORICAL_DATA_URL": "https://example.com/history.csv"}
        )
        self.assertEqual(remote["kind"], "remote")
        self.assertEqual(remote["display_label"], "Remote historical data")
        self.assertIsNone(remote["local_path"])

        self.assertEqual(resolve_historical_source({})["kind"], "missing")

    def test_local_error_details_do_not_expose_the_configured_path(self):
        source = resolve_historical_source(
            {"BWB_HISTORICAL_CSV": "/Users/example/private/HistoricalData.csv"}
        )
        detail = safe_historical_error_detail(
            OSError("Could not open /Users/example/private/HistoricalData.csv"), source
        )
        self.assertNotIn("/Users/example", detail)
        self.assertIn("local historical CSV", detail)


if __name__ == "__main__":
    unittest.main()
