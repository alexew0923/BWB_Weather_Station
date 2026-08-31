"""Stage 1: remote ingestion, URL handling and structured failures."""

import io
import unittest
from urllib.error import HTTPError, URLError

from tests.support import HEADER  # noqa: F401  (ensures sys.path is prepared)

from environmental.config import IngestionConfig
from environmental.data_sources import (
    fetch_csv_text,
    normalize_sheet_url,
    read_local_csv_text,
    resolve_historical_source,
    resolve_live_source,
)
from environmental.errors import (
    ConfigurationError,
    SourceFormatError,
    SourceUnavailableError,
)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
        return False


def opener_returning(payload):
    def opener(url, timeout=None):
        opener.url = url
        opener.timeout = timeout
        return FakeResponse(payload)
    opener.url = None
    return opener


def opener_raising(error):
    def opener(url, timeout=None):
        raise error
    return opener


class NormalizeSheetUrlTests(unittest.TestCase):
    def test_edit_url_becomes_csv_export(self):
        self.assertEqual(
            normalize_sheet_url(
                "https://docs.google.com/spreadsheets/d/SHEET/edit?gid=42#gid=42"
            ),
            "https://docs.google.com/spreadsheets/d/SHEET/export?format=csv&gid=42",
        )

    def test_gid_may_come_from_the_fragment_alone(self):
        self.assertEqual(
            normalize_sheet_url(
                "https://docs.google.com/spreadsheets/d/SHEET/edit#gid=7"
            ),
            "https://docs.google.com/spreadsheets/d/SHEET/export?format=csv&gid=7",
        )

    def test_existing_export_url_is_unchanged(self):
        url = "https://docs.google.com/spreadsheets/d/SHEET/export?format=csv&gid=3"
        self.assertEqual(normalize_sheet_url(url), url)

    def test_gviz_url_passes_through(self):
        url = (
            "https://docs.google.com/spreadsheets/d/SHEET/gviz/tq"
            "?tqx=out:csv&sheet=HistoricalData"
        )
        self.assertEqual(normalize_sheet_url(url), url)

    def test_non_google_csv_url_passes_through(self):
        self.assertEqual(
            normalize_sheet_url("https://example.test/data.csv"),
            "https://example.test/data.csv",
        )

    def test_non_http_scheme_is_a_configuration_error(self):
        with self.assertRaises(ConfigurationError):
            normalize_sheet_url("file:///etc/passwd")

    def test_garbage_is_a_configuration_error_not_a_traceback(self):
        with self.assertRaises(ConfigurationError) as caught:
            normalize_sheet_url("not-a-url", setting="HISTORICAL_DATA_URL")
        self.assertIn("HISTORICAL_DATA_URL", caught.exception.detail)

    def test_google_url_without_a_numeric_gid_is_rejected(self):
        with self.assertRaises(ConfigurationError):
            normalize_sheet_url(
                "https://docs.google.com/spreadsheets/d/SHEET/edit?gid=abc"
            )


class ResolveSourceTests(unittest.TestCase):
    def setUp(self):
        self.config = IngestionConfig()

    def test_missing_variable_names_the_setting(self):
        with self.assertRaises(ConfigurationError) as caught:
            resolve_historical_source(self.config, environ={})
        self.assertIn("HISTORICAL_DATA_URL", caught.exception.detail)

    def test_remote_source_is_selected_by_default(self):
        source = resolve_historical_source(
            self.config, environ={"HISTORICAL_DATA_URL": "https://example.test/a.csv"}
        )
        self.assertEqual(source.kind, "remote")
        self.assertEqual(source.url, "https://example.test/a.csv")

    def test_local_override_is_only_used_when_explicitly_set(self):
        environ = {
            "HISTORICAL_DATA_URL": "https://example.test/a.csv",
            "BWB_ENVIRONMENTAL_CSV": "/tmp/dev.csv",
        }
        self.assertEqual(
            resolve_historical_source(self.config, environ=environ).kind, "local"
        )
        self.assertEqual(
            resolve_historical_source(
                self.config, environ=environ, allow_local_override=False
            ).kind,
            "remote",
        )

    def test_live_source_never_honours_the_local_override(self):
        source = resolve_live_source(
            self.config,
            environ={
                "STATIONWATCH_SHEET_URL": "https://example.test/live.csv",
                "BWB_ENVIRONMENTAL_CSV": "/tmp/dev.csv",
            },
        )
        self.assertEqual(source.kind, "remote")

    def test_source_description_hides_the_url(self):
        source = resolve_historical_source(
            self.config, environ={"HISTORICAL_DATA_URL": "https://secret.test/a.csv"}
        )
        self.assertNotIn("secret", source.describe())
        self.assertNotIn("url", source.to_dict())


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.config = IngestionConfig()

    def test_valid_csv_is_returned_and_the_url_is_normalised(self):
        opener = opener_returning(b"Date,Rain Value\n2026-05-01 00:00:00,4095\n")
        text = fetch_csv_text(
            "https://docs.google.com/spreadsheets/d/S/edit?gid=9",
            config=self.config,
            opener=opener,
        )
        self.assertIn("Rain Value", text)
        self.assertIn("export?format=csv&gid=9", opener.url)

    def test_byte_order_mark_is_stripped(self):
        opener = opener_returning("﻿Date,Rain Value\n".encode("utf-8"))
        text = fetch_csv_text("https://example.test/a.csv", self.config, opener)
        self.assertTrue(text.startswith("Date"))

    def test_http_error_becomes_source_unavailable(self):
        error = HTTPError("u", 404, "Not Found", {}, None)
        self.addCleanup(error.close)
        with self.assertRaises(SourceUnavailableError) as caught:
            fetch_csv_text(
                "https://example.test/a.csv", self.config, opener_raising(error)
            )
        self.assertIn("404", caught.exception.detail)

    def test_unreachable_host_becomes_source_unavailable(self):
        opener = opener_raising(URLError("no route to host"))
        with self.assertRaises(SourceUnavailableError):
            fetch_csv_text("https://example.test/a.csv", self.config, opener)

    def test_timeout_becomes_source_unavailable(self):
        opener = opener_raising(TimeoutError("timed out"))
        with self.assertRaises(SourceUnavailableError):
            fetch_csv_text("https://example.test/a.csv", self.config, opener)

    def test_timeout_is_passed_to_the_opener(self):
        opener = opener_returning(b"Date,Rain Value\n")
        fetch_csv_text("https://example.test/a.csv", self.config, opener)
        self.assertEqual(opener.timeout, self.config.http_timeout_seconds)

    def test_html_response_is_a_format_error(self):
        opener = opener_returning(b"<!DOCTYPE html><html><body>Sign in</body></html>")
        with self.assertRaises(SourceFormatError) as caught:
            fetch_csv_text("https://example.test/a.csv", self.config, opener)
        self.assertIn("HTML", caught.exception.detail)

    def test_empty_response_is_a_format_error(self):
        opener = opener_returning(b"   \n")
        with self.assertRaises(SourceFormatError):
            fetch_csv_text("https://example.test/a.csv", self.config, opener)

    def test_oversized_response_is_refused(self):
        config = IngestionConfig(max_response_bytes=16)
        opener = opener_returning(b"Date,Rain Value\n" + b"x" * 1000)
        with self.assertRaises(SourceFormatError):
            fetch_csv_text("https://example.test/a.csv", config, opener)

    def test_invalid_utf8_is_a_format_error(self):
        opener = opener_returning(b"Date,Rain\n\xff\xfe\n")
        with self.assertRaises(SourceFormatError):
            fetch_csv_text("https://example.test/a.csv", self.config, opener)

    def test_missing_local_file_is_source_unavailable(self):
        with self.assertRaises(SourceUnavailableError):
            read_local_csv_text("/nonexistent/path/to/telemetry.csv")


if __name__ == "__main__":
    unittest.main()
