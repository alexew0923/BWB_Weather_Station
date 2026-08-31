"""Stage 2: canonicalisation, schema validation and quality masking."""

import unittest
from dataclasses import replace
from datetime import datetime, timedelta

import pandas as pd

from tests.support import HEADER, START, csv_text, dataset_from, row, series_csv

from environmental import sensors
from environmental.config import EnvironmentalConfig, IngestionConfig
from environmental.dataset import (
    MULTI_SENSOR_FAULT,
    SHT4X_FAULT,
    build_dataset_from_csv_text,
)
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


class SHT4xFaultSignatureTests(unittest.TestCase):
    """The device fault signature observed at this station (audit ENV-01).

    Fixtures reproduce the real pathology from the historical record rather
    than an invented one:

      * ``(0.00 degC, 1.97 %)`` -- 36 rows, 2026-04-02 to 2026-04-08 07:54
      * ``(missing, 1.97 %)``   -- 38 rows, 2026-04-08 14:11 onward, after the
        ingestion script began blanking temperature zeros
      * ``(0.00 degC, 0.00 %)`` -- 15 rows, 2025-11-17
      * ``(23.08 degC, 0.00 %)`` -- 1 row; deliberately NOT a fault frame

    In all 90 fault occurrences the temperature half is either exactly zero or
    absent, never a plausible reading.
    """

    def test_zero_temperature_with_fault_humidity_invalidates_both(self):
        dataset = dataset_from(csv_text([row(START, temperature=0.0, humidity=1.97)]))
        self.assertEqual(dataset.report.sensor_fault_frames, 1)
        self.assertEqual(dataset.valid_count(sensors.TEMPERATURE), 0)
        self.assertEqual(dataset.valid_count(sensors.HUMIDITY), 0)

    def test_missing_temperature_with_fault_humidity_invalidates_humidity(self):
        # The post-blanking form: the ingestion script removed the temperature
        # half of the same fault, so only the humidity half reaches the sheet.
        dataset = dataset_from(csv_text([row(START, temperature=None, humidity=1.97)]))
        self.assertEqual(dataset.report.sensor_fault_frames, 1)
        self.assertEqual(dataset.valid_count(sensors.HUMIDITY), 0)

    def test_zero_temperature_with_zero_humidity_is_a_fault_frame(self):
        dataset = dataset_from(csv_text([row(START, temperature=0.0, humidity=0.0)]))
        self.assertEqual(dataset.report.sensor_fault_frames, 1)
        self.assertEqual(dataset.valid_count(sensors.TEMPERATURE), 0)
        self.assertEqual(dataset.valid_count(sensors.HUMIDITY), 0)

    def test_a_genuine_zero_celsius_reading_survives(self):
        # Freezing point with an ordinary humidity beside it is weather, not a
        # fault. This is the reading the rule exists to protect.
        dataset = dataset_from(csv_text([row(START, temperature=0.0, humidity=86.4)]))
        self.assertEqual(dataset.report.sensor_fault_frames, 0)
        self.assertEqual(dataset.valid_count(sensors.TEMPERATURE), 1)
        self.assertEqual(dataset.valid_count(sensors.HUMIDITY), 1)

    def test_freezing_temperature_with_plausible_humidity_stays_valid(self):
        # The spec case: 0.0 degC is ordinary winter weather here and must
        # survive across the whole plausible humidity range.
        for humidity in (75.0, 40.0, 86.4, 100.0):
            with self.subTest(humidity=humidity):
                dataset = dataset_from(
                    csv_text([row(START, temperature=0.0, humidity=humidity)])
                )
                self.assertEqual(dataset.report.sensor_fault_frames, 0)
                self.assertEqual(dataset.valid_count(sensors.TEMPERATURE), 1)
                self.assertEqual(dataset.valid_count(sensors.HUMIDITY), 1)

    def test_a_very_low_humidity_that_is_not_a_fault_code_is_kept(self):
        # Proves this is a signature rule and not a "low humidity is
        # impossible" rule: 3.5 % is lower than one of the fault codes and is
        # still admitted, because nothing pairs with it.
        dataset = dataset_from(csv_text([row(START, temperature=12.0, humidity=3.5)]))
        self.assertEqual(dataset.report.sensor_fault_frames, 0)
        self.assertEqual(dataset.valid_count(sensors.HUMIDITY), 1)

    def test_a_fault_code_humidity_with_a_plausible_temperature_is_kept(self):
        # 1.97 alone, beside a real temperature, is not the signature.
        dataset = dataset_from(csv_text([row(START, temperature=12.0, humidity=1.97)]))
        self.assertEqual(dataset.report.sensor_fault_frames, 0)
        self.assertEqual(dataset.valid_count(sensors.HUMIDITY), 1)

    def test_low_humidity_alone_is_not_treated_as_a_device_fault(self):
        # Without the paired temperature signature there is no evidence of a
        # device fault, so the engine must not invent one. Physical
        # plausibility is a separate concern and does not exclude this value.
        dataset = dataset_from(csv_text([row(START, temperature=23.08, humidity=0.0)]))
        self.assertEqual(dataset.report.sensor_fault_frames, 0)
        self.assertEqual(dataset.valid_count(sensors.TEMPERATURE), 1)
        self.assertEqual(dataset.valid_count(sensors.HUMIDITY), 1)

    def test_ordinary_humidity_with_zero_temperature_is_not_a_fault(self):
        dataset = dataset_from(csv_text([row(START, temperature=0.0, humidity=1.97)]))
        self.assertEqual(dataset.report.sensor_fault_frames, 1)
        other = dataset_from(csv_text([row(START, temperature=0.0, humidity=2.5)]))
        self.assertEqual(other.report.sensor_fault_frames, 0)

    def test_fault_detection_is_configurable(self):
        config = EnvironmentalConfig()
        config = config.with_overrides(
            quality=replace(
                config.quality, treat_sht4x_fault_frames_as_invalid=False
            )
        )
        dataset = dataset_from(
            csv_text([row(START, temperature=0.0, humidity=1.97)]), config=config
        )
        self.assertEqual(dataset.report.sensor_fault_frames, 0)
        self.assertEqual(dataset.valid_count(sensors.HUMIDITY), 1)

    def test_fault_frames_are_masked_not_deleted(self):
        # Same convention as every other quality rule: the row still arrived,
        # so it still counts towards delivery completeness.
        dataset = dataset_from(
            csv_text([
                row(START, temperature=0.0, humidity=1.97),
                row(START + timedelta(minutes=5), temperature=11.0, humidity=80.0),
            ])
        )
        self.assertEqual(len(dataset), 2)
        self.assertEqual(dataset.report.sensor_fault_frames, 1)
        self.assertEqual(dataset.valid_count(sensors.TEMPERATURE), 1)

    def test_fault_count_is_reported_and_serialisable(self):
        dataset = dataset_from(csv_text([row(START, temperature=0.0, humidity=1.97)]))
        report = dataset.report.to_dict()
        self.assertEqual(report["sensor_fault_frames"], 1)
        self.assertEqual(
            report["sensor_fault_signatures"], {SHT4X_FAULT: 1}
        )
        self.assertTrue(any(SHT4X_FAULT in note for note in dataset.report.notes))

    def test_the_reason_code_is_recorded_per_row(self):
        dataset = dataset_from(
            csv_text([
                row(START, temperature=0.0, humidity=1.97),
                row(START + timedelta(minutes=5), temperature=11.0, humidity=80.0),
            ])
        )
        self.assertEqual(list(dataset.fault_reasons), [SHT4X_FAULT, ""])

    def test_raw_values_are_preserved_for_a_fault_frame(self):
        # Masked, never mutated: the export must still reconcile against the
        # source, and a defect has to stay countable.
        dataset = dataset_from(csv_text([row(START, temperature=0.0, humidity=1.97)]))
        self.assertEqual(dataset.frame[sensors.HUMIDITY].iloc[0], 1.97)
        self.assertEqual(dataset.frame[sensors.TEMPERATURE].iloc[0], 0.0)
        self.assertTrue(pd.isna(dataset.series(sensors.HUMIDITY).iloc[0]))


class MultiSensorFaultSignatureTests(unittest.TestCase):
    """Audit ENV-01: temperature, humidity and pressure all zero together.

    Fifteen rows in the historical record carry this, all inside
    2025-11-17 12:25-14:58 with the boot counter restarted at 2, 3, 4, 6, 14 --
    i.e. immediately after a power-loss reboot. Two independent parts reporting
    exactly zero in the same frame is an instrument failure, not weather.
    """

    def test_all_three_zero_is_a_multi_sensor_fault(self):
        dataset = dataset_from(
            csv_text([row(START, temperature=0.0, humidity=0.0, pressure=0.0)])
        )
        self.assertEqual(
            dataset.report.sensor_fault_signatures, {MULTI_SENSOR_FAULT: 1}
        )
        self.assertEqual(list(dataset.fault_reasons), [MULTI_SENSOR_FAULT])

    def test_pressure_is_excluded_too(self):
        dataset = dataset_from(
            csv_text([row(START, temperature=0.0, humidity=0.0, pressure=0.0)])
        )
        for channel in (sensors.TEMPERATURE, sensors.HUMIDITY, sensors.PRESSURE):
            self.assertEqual(dataset.valid_count(channel), 0)

    def test_the_more_specific_signature_wins(self):
        # This frame matches the SHT4x pair as well; the reason recorded must be
        # the multi-sensor one, because blaming a single part would understate
        # what failed.
        dataset = dataset_from(
            csv_text([row(START, temperature=0.0, humidity=0.0, pressure=0.0)])
        )
        self.assertNotIn(SHT4X_FAULT, dataset.report.sensor_fault_signatures)

    def test_other_devices_on_the_frame_are_untouched(self):
        dataset = dataset_from(
            csv_text([
                row(START, temperature=0.0, humidity=0.0, pressure=0.0,
                    wetness=4095.0, soil=1900.0)
            ])
        )
        self.assertEqual(dataset.valid_count(sensors.WETNESS_SIGNAL), 1)
        self.assertEqual(dataset.valid_count(sensors.SOIL_SIGNAL), 1)

    def test_two_zeros_without_the_third_are_not_a_multi_sensor_fault(self):
        # Temperature and humidity zero with a plausible pressure is the SHT4x
        # signature, not the whole-frame one.
        dataset = dataset_from(
            csv_text([row(START, temperature=0.0, humidity=0.0, pressure=1010.0)])
        )
        self.assertEqual(
            dataset.report.sensor_fault_signatures, {SHT4X_FAULT: 1}
        )
        self.assertEqual(dataset.valid_count(sensors.PRESSURE), 1)

    def test_multi_sensor_detection_is_configurable(self):
        config = EnvironmentalConfig()
        config = config.with_overrides(
            quality=replace(
                config.quality, treat_multi_sensor_zero_frames_as_invalid=False
            )
        )
        dataset = dataset_from(
            csv_text([row(START, temperature=0.0, humidity=0.0, pressure=0.0)]),
            config=config,
        )
        # Falls back to the SHT4x signature, which still matches this frame.
        self.assertEqual(
            dataset.report.sensor_fault_signatures, {SHT4X_FAULT: 1}
        )

    def test_other_channels_on_a_fault_frame_are_untouched(self):
        # Only the SHT4x pair is excluded; the wetness channel on the same row
        # comes from a different device and is still a reading.
        dataset = dataset_from(
            csv_text([row(START, temperature=0.0, humidity=1.97, wetness=4095.0)])
        )
        self.assertEqual(dataset.valid_count(sensors.WETNESS_SIGNAL), 1)


if __name__ == "__main__":
    unittest.main()
