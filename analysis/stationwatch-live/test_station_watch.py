import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
from zoneinfo import ZoneInfo

import station_watch


HALIFAX = ZoneInfo("America/Halifax")
LATEST = datetime(2026, 8, 28, 20, 0, tzinfo=HALIFAX)
CHECKED = datetime(2026, 8, 28, 20, 5, tzinfo=HALIFAX)


class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.state_path = Path(self.directory.name) / "state.json"
        self.alerts = []

    def tearDown(self):
        self.directory.cleanup()

    def record(self, status):
        return station_watch.record_result(
            status,
            LATEST,
            CHECKED,
            300,
            notifier=lambda new_status, *_: self.alerts.append(new_status),
            state_path=self.state_path,
        )

    def test_required_status_transitions(self):
        self.assertFalse(self.record("HEALTHY"))  # establish initial state
        self.assertFalse(self.record("HEALTHY"))
        self.assertTrue(self.record("DELAYED"))
        self.assertTrue(self.record("OFFLINE"))
        self.assertFalse(self.record("OFFLINE"))
        self.assertTrue(self.record("HEALTHY"))
        self.assertEqual(self.alerts, ["DELAYED", "OFFLINE", "HEALTHY"])


class CheckerTests(unittest.TestCase):
    def test_threshold_classification(self):
        self.assertEqual(station_watch.status_for(5), "HEALTHY")
        self.assertEqual(station_watch.status_for(20), "DELAYED")
        self.assertEqual(station_watch.status_for(40), "OFFLINE")

    def test_invalid_final_row_is_ignored(self):
        text = (
            "Timestamp,Temperature\n"
            "2026-08-28 20:00:00,20\n"
            "2026-08-28 20:05:00,21\n"
            "invalid trailing row,22\n"
        )
        self.assertEqual(station_watch.newest_timestamp(text), LATEST.replace(minute=5))

    def test_download_failure_is_monitor_error(self):
        with patch.object(station_watch, "download_csv", side_effect=URLError("network unavailable")):
            with patch("builtins.print") as output:
                result = station_watch.main([])
        self.assertEqual(result, 1)
        printed = " ".join(str(call.args[0]) for call in output.call_args_list)
        self.assertIn("MONITOR ERROR", printed)
        self.assertIn("could not be determined", printed)
        self.assertNotIn("Status: OFFLINE", printed)


if __name__ == "__main__":
    unittest.main()
