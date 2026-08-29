"""Focused integration checks for the Incident Explorer page and service."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest


APP_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = APP_DIR.parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from services import incident_service  # noqa: E402


APP_PATH = APP_DIR / "app_pages" / "incidents.py"
HISTORICAL_CSV = (
    REPOSITORY_ROOT / "analysis" / "reliability-audit" / "data" / "HistoricalData.csv"
)
SENSOR_COLUMNS = [
    "Temperature",
    "Humidity",
    "Soil Moisture",
    "Air Pressure",
    "Rain Value",
    "Battery Voltage",
]


def write_nominal_history(path):
    rows = []
    for count, timestamp in enumerate(pd.date_range("2026-05-01 08:00", periods=5, freq="5min"), 1):
        row = {"Date": timestamp.strftime("%Y-%m-%d %H:%M:%S"), "Count": count}
        row.update({column: 10.0 for column in SENSOR_COLUMNS})
        row["Air Pressure"] = 1000.0
        row["Battery Voltage"] = 3800.0
        rows.append(row)
    pd.DataFrame(rows).to_csv(path, index=False)


class IncidentServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = incident_service.load_incident_catalog(HISTORICAL_CSV)

    def test_incident_list_loads_from_historical_source(self):
        self.assertFalse(self.catalog["incidents"].empty)
        self.assertTrue(
            {"gap_start", "gap_end", "gap_hours", "severity"}.issubset(
                self.catalog["incidents"].columns
            )
        )

    def test_selected_incident_uses_existing_evidence_contract(self):
        selected = self.catalog["incidents"].iloc[-1].to_dict()
        report = incident_service.analyze_selected_incident(
            self.catalog["frame"], selected, source=str(HISTORICAL_CSV)
        )
        self.assertEqual(
            set(report["interpretation"]),
            {"observed", "suggestive", "not_determinable"},
        )
        self.assertEqual(report["incident"]["received_readings"], 0)

    def test_nominal_history_has_no_significant_incidents(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "nominal.csv"
            write_nominal_history(source)
            catalog = incident_service.load_incident_catalog(source)
        self.assertTrue(catalog["incidents"].empty)


class IncidentPageTests(unittest.TestCase):
    def setUp(self):
        self.previous_local = os.environ.get("BWB_HISTORICAL_CSV")
        self.previous_url = os.environ.get("HISTORICAL_DATA_URL")
        os.environ["BWB_HISTORICAL_CSV"] = str(HISTORICAL_CSV)

    def tearDown(self):
        if self.previous_local is None:
            os.environ.pop("BWB_HISTORICAL_CSV", None)
        else:
            os.environ["BWB_HISTORICAL_CSV"] = self.previous_local
        if self.previous_url is None:
            os.environ.pop("HISTORICAL_DATA_URL", None)
        else:
            os.environ["HISTORICAL_DATA_URL"] = self.previous_url

    def run_app(self, timeout=60):
        return AppTest.from_file(str(APP_PATH), default_timeout=timeout).run(
            timeout=timeout
        )

    def assert_no_exceptions(self, app):
        self.assertEqual([item.value for item in app.exception], [])

    def test_page_loads_and_incident_selection_reruns(self):
        app = self.run_app()
        self.assert_no_exceptions(app)
        self.assertIn("Incident Explorer", [item.value for item in app.title])
        self.assertGreater(len(app.selectbox[0].options), 1)
        initial = app.selectbox[0].value
        app.selectbox[0].set_value(initial - 1)
        app.run(timeout=60)
        self.assert_no_exceptions(app)
        self.assertEqual(app.selectbox[0].value, initial - 1)
        self.assertIn("Evidence assessment", [item.value for item in app.subheader])

    def test_local_override_precedes_remote_configuration(self):
        os.environ["HISTORICAL_DATA_URL"] = "not-a-url"
        app = self.run_app()
        self.assert_no_exceptions(app)
        self.assertIn("Incident Explorer", [item.value for item in app.title])
        self.assertTrue(
            any("Local historical data" in item.value for item in app.caption)
        )
        self.assertFalse(
            any(str(HISTORICAL_CSV.resolve()) in item.value for item in app.caption)
        )

    def test_no_incidents_is_a_clean_empty_state(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "nominal.csv"
            write_nominal_history(source)
            os.environ["BWB_HISTORICAL_CSV"] = str(source)
            app = self.run_app()
        self.assert_no_exceptions(app)
        self.assertTrue(
            any("No significant outages" in item.value for item in app.info)
        )

    def test_missing_configuration_is_actionable(self):
        os.environ.pop("BWB_HISTORICAL_CSV", None)
        os.environ.pop("HISTORICAL_DATA_URL", None)
        app = self.run_app()
        self.assert_no_exceptions(app)
        self.assertTrue(any("not configured" in item.value.lower() for item in app.error))
        self.assertTrue(any("HISTORICAL_DATA_URL" in item.value for item in app.caption))

    def test_missing_local_source_does_not_expose_its_path(self):
        missing_path = "/Users/example/private/bwb-source-does-not-exist.csv"
        os.environ["BWB_HISTORICAL_CSV"] = missing_path
        app = self.run_app()
        self.assert_no_exceptions(app)
        self.assertTrue(any("could not be found" in item.value for item in app.error))
        self.assertFalse(any(missing_path in item.value for item in app.caption))
        self.assertFalse(any(missing_path in item.value for item in app.code))

    def test_malformed_remote_url_is_isolated(self):
        os.environ.pop("BWB_HISTORICAL_CSV", None)
        os.environ["HISTORICAL_DATA_URL"] = "not-a-url"
        app = self.run_app()
        self.assert_no_exceptions(app)
        self.assertTrue(any("URL is malformed" in item.value for item in app.error))
        self.assertTrue(
            any("Remote historical data" in item.value for item in app.caption)
        )

    def test_remote_fetch_failure_is_isolated(self):
        os.environ.pop("BWB_HISTORICAL_CSV", None)
        os.environ["HISTORICAL_DATA_URL"] = "https://example.invalid/incidents.csv"
        error = incident_service.HistoricalDataError(
            "The historical telemetry Sheet could not be retrieved.", "network down"
        )
        with patch("services.incident_service.fetch_historical_csv", side_effect=error):
            app = self.run_app()
        self.assert_no_exceptions(app)
        self.assertTrue(any("could not be retrieved" in item.value for item in app.error))

    def test_incident_analysis_failure_does_not_crash_page(self):
        with patch(
            "services.incident_service.analyze_incident",
            side_effect=ValueError("synthetic incident failure"),
        ):
            app = self.run_app()
        self.assert_no_exceptions(app)
        self.assertTrue(
            any("selected incident could not be analyzed" in item.value.lower() for item in app.error)
        )
        self.assertIn("Incident list", [item.value for item in app.subheader])


if __name__ == "__main__":
    unittest.main()
