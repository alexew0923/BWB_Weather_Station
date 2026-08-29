"""Streamlit interaction and failure-state checks for the research dashboard."""

import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parent / "app.py"


class DashboardTests(unittest.TestCase):
    def run_app(self, timeout=45):
        return AppTest.from_file(str(APP_PATH), default_timeout=timeout).run(
            timeout=timeout
        )

    def assert_no_exceptions(self, app):
        self.assertEqual([item.value for item in app.exception], [])

    def test_dashboard_loads_and_primary_controls_rerun(self):
        app = self.run_app()
        self.assert_no_exceptions(app)
        self.assertIn("Battery & energy research", [item.value for item in app.title])
        self.assertEqual(app.segmented_control[0].value, "Full")
        self.assertGreater(len(app.selectbox[0].options), 1)

        app.segmented_control[0].set_value("7 days")
        app.run(timeout=45)
        self.assert_no_exceptions(app)
        self.assertEqual(app.segmented_control[0].value, "7 days")

        app.segmented_control[0].set_value("Custom")
        app.run(timeout=45)
        app.date_input[0].set_value((date(2026, 7, 1), date(2026, 7, 7)))
        app.run(timeout=45)
        self.assert_no_exceptions(app)
        self.assertEqual(
            app.date_input[0].value,
            (date(2026, 7, 1), date(2026, 7, 7)),
        )

        app.selectbox[0].set_value(0)
        app.run(timeout=45)
        self.assert_no_exceptions(app)
        self.assertEqual(app.selectbox[0].value, 0)

    def test_energy_form_rejects_missing_fields_and_accepts_explicit_inputs(self):
        app = self.run_app()
        app.button[0].click()
        app.run(timeout=45)
        self.assertTrue(
            any("Complete every field" in item.value for item in app.error)
        )

        for index, value in enumerate((10.0, 0.1, 10.0, 290.0, 288.0)):
            app.number_input[index].set_value(value)
        app.button[0].click()
        app.run(timeout=45)
        self.assert_no_exceptions(app)
        self.assertTrue(
            any("Uncalibrated model completed" in item.value for item in app.success)
        )
        metric_labels = [item.label for item in app.metric]
        self.assertIn("Modeled daily charge consumption", metric_labels)
        self.assertNotIn("Remaining runtime", metric_labels)

        app.number_input[8].set_value(2.0)
        app.button[0].click()
        app.run(timeout=45)
        self.assertTrue(
            any("must be supplied together" in item.value for item in app.error)
        )

    def test_missing_source_has_an_actionable_error_state(self):
        previous = os.environ.get("BWB_HISTORICAL_CSV")
        os.environ["BWB_HISTORICAL_CSV"] = "/tmp/bwb-source-does-not-exist.csv"
        try:
            app = self.run_app()
        finally:
            if previous is None:
                os.environ.pop("BWB_HISTORICAL_CSV", None)
            else:
                os.environ["BWB_HISTORICAL_CSV"] = previous
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("could not be found" in item.value for item in app.error))

    def test_invalid_source_schema_fails_without_a_traceback(self):
        previous = os.environ.get("BWB_HISTORICAL_CSV")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "invalid.csv"
            source.write_text("Date,Count\n2026-01-01 00:00:00,1\n", encoding="utf-8")
            os.environ["BWB_HISTORICAL_CSV"] = str(source)
            try:
                app = self.run_app()
            finally:
                if previous is None:
                    os.environ.pop("BWB_HISTORICAL_CSV", None)
                else:
                    os.environ["BWB_HISTORICAL_CSV"] = previous
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(
            any("could not be analyzed safely" in item.value for item in app.error)
        )


if __name__ == "__main__":
    unittest.main()
