"""The public API surface and the CLI."""

import io
import json
import unittest
from contextlib import redirect_stdout
from datetime import timedelta

from tests.support import (
    DRY_RAIL,
    START,
    csv_text,
    dataset_from,
    humid_during,
    row,
    series_csv,
    wet_profile,
)

from environmental import api, cli
from environmental.config import EnvironmentalConfig
from environmental.errors import ConfigurationError
from environmental.models import SoilResponseStatus, to_serialisable


DOCUMENT = series_csv(
    START,
    2000,
    wetness=wet_profile(800, 40, 1160),
    humidity=humid_during(800, 40),
    soil=lambda index, moment: 1900.0 + (400.0 if index >= 810 else 0.0),
)


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = dataset_from(DOCUMENT)
        cls.summary = api.analyze_environment(cls.dataset)

    def test_the_expected_end_state_workflow_works(self):
        dataset = self.dataset
        summary = api.analyze_environment(dataset)
        events = summary.events
        self.assertTrue(events)
        event = api.get_event(events, events[0].event_id)
        self.assertTrue(str(event.classification))
        self.assertTrue(str(event.evidence_strength))
        self.assertTrue(str(event.soil_response.status))

    def test_list_events_filters_by_soil_status(self):
        detected = api.list_events(
            self.summary.events, soil_status=SoilResponseStatus.DETECTED
        )
        self.assertEqual(len(detected), 1)

    def test_list_events_filters_by_time(self):
        first = self.summary.events[0]
        self.assertEqual(
            len(api.list_events(self.summary.events, start=first.end_time)), 1
        )
        self.assertEqual(
            len(api.list_events(
                self.summary.events, end=first.start_time - timedelta(days=1)
            )),
            0,
        )

    def test_no_dataframe_crosses_the_api_boundary(self):
        payload = to_serialisable(self.summary)
        json.dumps(payload)
        self.assertIsInstance(payload["events"], list)
        self.assertIsInstance(payload["profiles"], dict)

    def test_missing_configuration_raises_a_domain_error(self):
        with self.assertRaises(ConfigurationError):
            api.get_environmental_summary(environ={})

    def test_the_remote_source_is_fetched_exactly_once_per_run(self):
        calls = []

        def counting_opener(url, timeout=None):
            calls.append(url)
            handle = io.BytesIO(DOCUMENT.encode("utf-8"))
            handle.__enter__ = lambda: handle
            handle.__exit__ = lambda *args: None
            return handle

        api.get_environmental_summary(
            opener=counting_opener,
            environ={"HISTORICAL_DATA_URL": "https://example.test/history.csv"},
        )
        self.assertEqual(len(calls), 1)

    def test_configuration_overrides_flow_through_the_api(self):
        from environmental.config import WetnessDetectorConfig

        strict = EnvironmentalConfig().with_overrides(
            wetness=WetnessDetectorConfig(enter_counts=3000.0, exit_counts=2500.0)
        )
        dataset = dataset_from(DOCUMENT, config=strict)
        self.assertEqual(api.detect_environmental_events(dataset, strict), ())


class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path

        cls.directory = tempfile.TemporaryDirectory()
        cls.path = Path(cls.directory.name) / "telemetry.csv"
        cls.path.write_text(DOCUMENT, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def run_cli(self, *arguments):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main(["--csv", str(self.path), *arguments])
        return code, buffer.getvalue()

    def test_profile_command(self):
        code, output = self.run_cli("profile")
        self.assertEqual(code, 0)
        self.assertIn("wetness_signal_raw", output)
        self.assertIn("uncalibrated", output)

    def test_events_command(self):
        code, output = self.run_cli("events")
        self.assertEqual(code, 0)
        self.assertIn("wetting-", output)

    def test_events_command_json_is_valid(self):
        code, output = self.run_cli("--json", "events")
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertTrue(payload)
        self.assertIn("event_id", payload[0])

    def test_event_command(self):
        _, listing = self.run_cli("--json", "events")
        event_id = json.loads(listing)[0]["event_id"]
        code, output = self.run_cli("event", event_id)
        self.assertEqual(code, 0)
        self.assertIn("Interpretation", output)
        self.assertIn("Soil response", output)

    def test_summary_command_lists_limitations(self):
        code, output = self.run_cli("summary")
        self.assertEqual(code, 0)
        self.assertIn("Limitations", output)
        self.assertIn("No rainfall depth", output)

    def test_baseline_command(self):
        code, output = self.run_cli("baseline")
        self.assertEqual(code, 0)
        self.assertIn("coverage", output)

    def test_validate_command_emits_json(self):
        code, output = self.run_cli("validate")
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertIn("dataset", payload)
        self.assertIn("versions", payload)

    def test_unknown_event_id_exits_non_zero_without_a_traceback(self):
        buffer = io.StringIO()
        errors = io.StringIO()
        import contextlib

        with redirect_stdout(buffer), contextlib.redirect_stderr(errors):
            code = cli.main(
                ["--csv", str(self.path), "event", "wetting-1999-01-01T00:00-0400"]
            )
        self.assertEqual(code, 1)
        self.assertIn("does not exist", errors.getvalue())

    def test_missing_source_exits_non_zero(self):
        import contextlib
        import os

        errors = io.StringIO()
        previous = os.environ.pop("HISTORICAL_DATA_URL", None)
        try:
            with contextlib.redirect_stderr(errors), redirect_stdout(io.StringIO()):
                code = cli.main(["events"])
        finally:
            if previous is not None:
                os.environ["HISTORICAL_DATA_URL"] = previous
        self.assertEqual(code, 1)
        self.assertIn("HISTORICAL_DATA_URL", errors.getvalue())

    def test_plots_command_writes_files(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            code, output = self.run_cli(
                "plots", "--output-dir", directory, "--event-plots", "1"
            )
            self.assertEqual(code, 0)
            written = list(Path(directory).glob("*.png"))
            self.assertGreaterEqual(len(written), 4)


if __name__ == "__main__":
    unittest.main()
