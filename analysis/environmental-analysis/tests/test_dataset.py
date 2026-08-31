"""Stage 2: canonicalisation, schema validation and quality masking."""

import unittest
from datetime import datetime, timedelta

import pandas as pd

from tests.support import HEADER, START, csv_text, dataset_from, row, series_csv

from environmental import sensors
from environmental.config import EnvironmentalConfig, IngestionConfig
from environmental.dataset import build_dataset_from_csv_text
from environmental.errors import EmptyDatasetError, SchemaError, SourceFormatError


class SchemaTests(unittest.TestCase):
    def test_header_only_sheet_is_an_empty_dataset_not_a_crash(self):
        with self.assertRaises(EmptyDatasetError) as caught:
            build_dataset_from_csv_text(HEADER + "\n")
        self.assertIn("header row", caught.exception.detail)

    def test_missing_timestamp_column_is_a_schema_error(self):
        with self.assertRaises(SchemaError):
            build_dataset_from_csv_text("Temperature,Rain Value\n1,2\n")

    def test_missing_wetness_column_is_a_schema_error(self):
        with self.assertRaises(SchemaError) as caught:
            build_dataset_from_csv_text("Date,Temperature\n2026-01-01 00:00:00,1\n")
        self.assertIn("Rain Value", caught.exception.detail)

    def test_timestamp_column_may_be_named_timestamp(self):
        text = "Timestamp,Rain Value\n2026-05-01 00:00:00,4095\n"
        dataset = build_dataset_from_csv_text(text)
        self.assertEqual(len(dataset), 1)

    def test_absent_optional_columns_are_reported_not_fatal(self):
        text = "Date,Rain Value\n2026-05-01 00:00:00,4095\n"
        dataset = build_dataset_from_csv_text(text)
        self.assertIn("Soil Moisture", dataset.report.missing_optional_columns)
        self.assertEqual(dataset.valid_count(sensors.SOIL_SIGNAL), 0)

    def test_unparseable_document_is_a_format_error(self):
        with self.assertRaises(SourceFormatError):
            build_dataset_from_csv_text('Date,Rain Value\n"unterminated\n')


class TimestampTests(unittest.TestCase):
    def test_timestamps_are_localised_to_the_station_timezone(self):
        dataset = dataset_from(series_csv(START, 3))
        self.assertEqual(str(dataset.timestamps.tz), "America/Halifax")

    def test_naive_timestamps_are_not_treated_as_utc(self):
        dataset = dataset_from(series_csv(datetime(2026, 5, 14, 12, 0), 1))
        self.assertEqual(dataset.start_time.hour, 12)
        self.assertEqual(dataset.start_time.utcoffset(), timedelta(hours=-3))

    def test_unparseable_timestamps_are_dropped_and_counted(self):
        text = csv_text(
            [
                row(START),
                "not-a-timestamp,1,2,3,4,4095,4000,2",
                row(START + timedelta(minutes=5)),
            ]
        )
        dataset = dataset_from(text)
        self.assertEqual(len(dataset), 2)
        self.assertEqual(dataset.report.unparseable_timestamps, 1)

    def test_all_timestamps_unparseable_is_an_empty_dataset(self):
        with self.assertRaises(EmptyDatasetError):
            dataset_from(csv_text(["bad,1,2,3,4,4095,4000,1"]))

    def test_rows_are_sorted_by_time_regardless_of_file_order(self):
        later = START + timedelta(minutes=30)
        dataset = dataset_from(csv_text([row(later, count=2), row(START, count=1)]))
        self.assertEqual(list(dataset.frame[sensors.BOOT_COUNT]), [1.0, 2.0])

    def test_duplicate_timestamps_keep_the_first_row_in_file_order(self):
        dataset = dataset_from(
            csv_text([row(START, temperature=10.0, count=1),
                      row(START, temperature=20.0, count=2)])
        )
        self.assertEqual(len(dataset), 1)
        self.assertEqual(dataset.report.duplicate_timestamps, 1)
        self.assertEqual(dataset.frame[sensors.TEMPERATURE].iloc[0], 10.0)

    def test_daylight_saving_fall_back_produces_real_elapsed_time(self):
        # Atlantic fall-back: 01:00 occurs twice on 2025-11-02.
        moments = [
            datetime(2025, 11, 2, 0, 55),
            datetime(2025, 11, 2, 1, 55),   # first pass, ADT
            datetime(2025, 11, 2, 1, 5),    # second pass, AST
            datetime(2025, 11, 2, 1, 58),
        ]
        dataset = dataset_from(
            csv_text([row(moment, count=index + 1)
                      for index, moment in enumerate(moments)])
        )
        gaps = dataset.inter_arrival_minutes()
        self.assertTrue((gaps > 0).all(), "no interval may run backwards")
        self.assertGreater(dataset.report.ambiguous_dst_timestamps, 0)

    def test_spring_forward_gap_does_not_create_a_backward_jump(self):
        moments = [
            datetime(2026, 3, 8, 1, 55),
            datetime(2026, 3, 8, 3, 5),
            datetime(2026, 3, 8, 3, 10),
        ]
        dataset = dataset_from(
            csv_text([row(moment, count=index + 1)
                      for index, moment in enumerate(moments)])
        )
        self.assertTrue((dataset.inter_arrival_minutes() > 0).all())


class CoercionAndMaskingTests(unittest.TestCase):
    def test_non_numeric_cells_are_coerced_and_counted(self):
        text = csv_text(
            [row(START, temperature="warm"), row(START + timedelta(minutes=5))]
        )
        dataset = dataset_from(text)
        self.assertEqual(dataset.report.non_numeric_cells[sensors.TEMPERATURE], 1)
        self.assertEqual(dataset.valid_count(sensors.TEMPERATURE), 1)

    def test_blank_cells_are_missing_not_malformed(self):
        dataset = dataset_from(csv_text([row(START, pressure=None)]))
        self.assertNotIn(sensors.PRESSURE, dataset.report.non_numeric_cells)
        self.assertEqual(dataset.valid_count(sensors.PRESSURE), 0)

    def test_impossible_values_are_masked_not_deleted(self):
        dataset = dataset_from(csv_text([row(START, pressure=4.04)]))
        self.assertEqual(len(dataset), 1)
        self.assertEqual(dataset.report.implausible_values[sensors.PRESSURE], 1)
        self.assertEqual(dataset.valid_count(sensors.PRESSURE), 0)
        # The original reading is still present for inspection.
        self.assertEqual(
            dataset.series(sensors.PRESSURE, valid_only=False).iloc[0], 4.04
        )

    def test_corrupt_frame_keeps_the_row_and_nulls_every_field(self):
        dataset = dataset_from(
            csv_text([row(START, soil=469762076, count=939531320)])
        )
        self.assertEqual(len(dataset), 1)
        self.assertEqual(dataset.report.corrupt_frames, 1)
        self.assertEqual(dataset.valid_count(sensors.SOIL_SIGNAL), 0)
        self.assertEqual(dataset.valid_count(sensors.TEMPERATURE), 0)

    def test_soil_zero_is_treated_as_ambiguous_by_default(self):
        dataset = dataset_from(csv_text([row(START, soil=0)]))
        self.assertEqual(dataset.valid_count(sensors.SOIL_SIGNAL), 0)

    def test_soil_zero_handling_is_configurable(self):
        config = EnvironmentalConfig()
        config = config.with_overrides(
            quality=type(config.quality)(treat_soil_zero_as_invalid=False)
        )
        dataset = dataset_from(csv_text([row(START, soil=0)]), config=config)
        self.assertEqual(dataset.valid_count(sensors.SOIL_SIGNAL), 1)

    def test_series_masks_invalid_readings(self):
        dataset = dataset_from(
            csv_text([row(START, pressure=4.04),
                      row(START + timedelta(minutes=5), pressure=1010.0)])
        )
        values = dataset.series(sensors.PRESSURE)
        self.assertTrue(pd.isna(values.iloc[0]))
        self.assertEqual(values.iloc[1], 1010.0)


class DatasetShapeTests(unittest.TestCase):
    def setUp(self):
        self.dataset = dataset_from(series_csv(START, 24))

    def test_subset_is_inclusive_and_returns_a_dataset(self):
        window = self.dataset.subset(
            self.dataset.start_time, self.dataset.start_time + pd.Timedelta("15min")
        )
        self.assertEqual(len(window), 4)
        self.assertEqual(window.config, self.dataset.config)

    def test_sampling_statistics_measure_the_observed_cadence(self):
        statistics = self.dataset.sampling_statistics()
        self.assertAlmostEqual(statistics["median_interval_minutes"], 5.0)
        self.assertEqual(statistics["gaps_over_continuity"], 0)

    def test_coverage_uses_the_audit_schedule(self):
        coverage = self.dataset.coverage()
        self.assertGreater(coverage["expected"], 0)
        self.assertLessEqual(coverage["fraction"], 1.0)

    def test_describe_is_serialisable(self):
        description = self.dataset.describe()
        self.assertEqual(description["rows"], 24)
        self.assertIn(sensors.WETNESS_SIGNAL, description["sensors"])

    def test_duplicate_policy_can_keep_the_last_row(self):
        config = EnvironmentalConfig().with_overrides(
            ingestion=IngestionConfig(duplicate_timestamp_policy="last")
        )
        dataset = dataset_from(
            csv_text([row(START, temperature=10.0), row(START, temperature=20.0)]),
            config=config,
        )
        self.assertEqual(dataset.frame[sensors.TEMPERATURE].iloc[0], 20.0)


if __name__ == "__main__":
    unittest.main()
