"""Focused checks for the unified shell and architecture boundary."""

import ast
import os
import sys
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest


APP_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = APP_DIR.parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


class ShellTests(unittest.TestCase):
    def test_overview_degrades_cleanly_when_live_source_is_malformed(self):
        previous = os.environ.get("STATIONWATCH_SHEET_URL")
        os.environ["STATIONWATCH_SHEET_URL"] = "not-a-url"
        try:
            app = AppTest.from_file(str(APP_DIR / "app.py"), default_timeout=20).run(
                timeout=20
            )
        finally:
            if previous is None:
                os.environ.pop("STATIONWATCH_SHEET_URL", None)
            else:
                os.environ["STATIONWATCH_SHEET_URL"] = previous
        self.assertEqual([item.value for item in app.exception], [])
        self.assertIn("Environmental monitoring system", [item.value for item in app.title])
        self.assertTrue(any(item.value == "MONITOR ERROR" for item in app.metric))

    def test_analysis_engines_do_not_import_streamlit(self):
        engine_dirs = (
            REPOSITORY_ROOT / "analysis" / "stationwatch-live",
            REPOSITORY_ROOT / "analysis" / "battery-energy-analysis",
            REPOSITORY_ROOT / "analysis" / "reliability-audit",
        )
        offenders = []
        for engine_dir in engine_dirs:
            for path in engine_dir.glob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import) and any(
                        alias.name == "streamlit" or alias.name.startswith("streamlit.")
                        for alias in node.names
                    ):
                        offenders.append(path)
                    if isinstance(node, ast.ImportFrom) and (
                        node.module == "streamlit"
                        or (node.module or "").startswith("streamlit.")
                    ):
                        offenders.append(path)
        self.assertEqual(offenders, [])

    def test_live_page_contains_monitor_error_without_exception(self):
        previous = os.environ.get("STATIONWATCH_SHEET_URL")
        os.environ["STATIONWATCH_SHEET_URL"] = "not-a-url"
        try:
            app = AppTest.from_file(
                str(APP_DIR / "app_pages" / "live.py"), default_timeout=20
            ).run(timeout=20)
        finally:
            if previous is None:
                os.environ.pop("STATIONWATCH_SHEET_URL", None)
            else:
                os.environ["STATIONWATCH_SHEET_URL"] = previous
        self.assertEqual([item.value for item in app.exception], [])
        self.assertTrue(
            any("monitoring failure" in item.value.lower() for item in app.caption)
        )


if __name__ == "__main__":
    unittest.main()
