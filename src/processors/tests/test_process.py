import unittest
from datetime import date, datetime
from unittest.mock import patch

from src.processors.process import Processor
from src.processors.tests._helpers import TempDirTestCase
from src.types import JobType, SourceType


class _StubProcessor(Processor):
    def __init__(self, job_type=JobType.DOWNLOAD):
        self.job_type = job_type
        self.source_type = SourceType.CITY_COUNCIL_MEETING
        self.redis_key = "stub.cc_mtg"
        super().__init__()


class TestExtractDateOrDatetime(unittest.TestCase):
    def setUp(self):
        self.processor = _StubProcessor()

    def test_returns_datetime_for_datetime_string(self):
        result = self.processor.extract_date_or_datetime("2026-05-08 07_00 PM")
        self.assertEqual(result, datetime(2026, 5, 8, 19, 0))
        self.assertIsInstance(result, datetime)

    def test_returns_date_for_date_string(self):
        result = self.processor.extract_date_or_datetime("2026-05-08")
        self.assertEqual(result, date(2026, 5, 8))
        self.assertNotIsInstance(result, datetime)

    def test_extracts_from_embedded_filename(self):
        result = self.processor.extract_date_or_datetime(
            "City Council Meeting 2026-05-08 07_00 PM - foo.mp4"
        )
        self.assertEqual(result, datetime(2026, 5, 8, 19, 0))

    def test_returns_none_when_no_match(self):
        self.assertIsNone(self.processor.extract_date_or_datetime("nothing useful"))


class TestExtractDatetimeObject(unittest.TestCase):
    def setUp(self):
        self.processor = _StubProcessor()

    def test_returns_datetime_for_datetime_string(self):
        result = self.processor.extract_datetime_object("2026-05-08 07_00 PM")
        self.assertEqual(result, datetime(2026, 5, 8, 19, 0))

    def test_promotes_date_to_midnight_datetime(self):
        result = self.processor.extract_datetime_object("2026-05-08")
        self.assertEqual(result, datetime(2026, 5, 8, 0, 0))
        self.assertIsInstance(result, datetime)

    def test_returns_none_when_no_match(self):
        self.assertIsNone(self.processor.extract_datetime_object("nothing"))


class TestConstructFilenameAndFilepath(unittest.TestCase):
    def setUp(self):
        self.processor = _StubProcessor(job_type=JobType.DOWNLOAD)

    def test_filename_for_date_uses_template(self):
        self.assertEqual(
            self.processor.construct_filename_for_date("2026-05-08"),
            "City Council Meeting 2026-05-08 - City of Clayton.mp4",
        )

    def test_filename_for_datetime_uses_template_verbatim(self):
        self.assertEqual(
            self.processor.construct_filename_for_date("2026-05-08 07_00 PM"),
            "City Council Meeting 2026-05-08 07_00 PM - City of Clayton.mp4",
        )

    def test_filename_for_other_job_type(self):
        self.assertEqual(
            self.processor.construct_filename_for_date(
                "2026-05-08", job_type=JobType.EXTRACT_AUDIO
            ),
            "City Council Meeting 2026-05-08 - City of Clayton.m4a",
        )

    def test_filepath_includes_job_dir(self):
        path = self.processor.construct_filepath_for_date("2026-05-08")
        self.assertTrue(
            path.endswith("City Council Meeting 2026-05-08 - City of Clayton.mp4")
        )


class TestGatherDates(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.processor = _StubProcessor()

    def test_extracts_dates_from_filenames(self):
        self.touch("City Council Meeting 2026-05-08 - City of Clayton.mp4")
        self.touch("City Council Meeting 2026-06-01 - City of Clayton.mp4")
        self.assertEqual(
            self.processor.gather_dates(self.tmpdir),
            ["2026-05-08", "2026-06-01"],
        )

    def test_extracts_datetimes_from_filenames(self):
        self.touch("City Council Meeting 2026-05-08 07_00 PM - City of Clayton.mp4")
        self.assertEqual(
            self.processor.gather_dates(self.tmpdir),
            ["2026-05-08 07_00 PM"],
        )

    def test_handles_mixed_date_and_datetime_filenames(self):
        self.touch("City Council Meeting 2026-05-08 - City of Clayton.mp4")
        self.touch("City Council Meeting 2026-06-01 07_00 PM - City of Clayton.mp4")
        self.assertEqual(
            self.processor.gather_dates(self.tmpdir),
            ["2026-05-08", "2026-06-01 07_00 PM"],
        )

    def test_skips_files_without_stub(self):
        self.touch("Other File 2026-05-08.mp4")
        self.assertEqual(self.processor.gather_dates(self.tmpdir), [])


class TestGetMostRecentMissingDates(unittest.TestCase):
    def test_returns_inputs_minus_outputs(self):
        processor = _StubProcessor()
        with (
            patch.object(
                processor,
                "gather_input_dates",
                return_value=["2026-05-08", "2026-06-01"],
            ),
            patch.object(processor, "gather_output_dates", return_value=["2026-05-08"]),
        ):
            self.assertEqual(processor.get_most_recent_missing_dates(), ["2026-06-01"])

    def test_treats_date_and_datetime_as_distinct(self):
        processor = _StubProcessor()
        with (
            patch.object(
                processor,
                "gather_input_dates",
                return_value=["2026-05-08", "2026-05-08 07_00 PM"],
            ),
            patch.object(processor, "gather_output_dates", return_value=["2026-05-08"]),
        ):
            self.assertEqual(
                processor.get_most_recent_missing_dates(), ["2026-05-08 07_00 PM"]
            )

    def test_empty_when_all_processed(self):
        processor = _StubProcessor()
        with (
            patch.object(processor, "gather_input_dates", return_value=["2026-05-08"]),
            patch.object(processor, "gather_output_dates", return_value=["2026-05-08"]),
        ):
            self.assertEqual(processor.get_most_recent_missing_dates(), [])

    def test_picks_up_gaps_in_the_middle_of_the_range(self):
        processor = _StubProcessor()
        inputs = [
            "2026-05-08",
            "2026-05-15",
            "2026-05-22",
            "2026-05-29",
            "2026-06-05",
        ]
        outputs = ["2026-05-08", "2026-05-15", "2026-05-29", "2026-06-05"]
        with (
            patch.object(processor, "gather_input_dates", return_value=inputs),
            patch.object(processor, "gather_output_dates", return_value=outputs),
        ):
            self.assertEqual(processor.get_most_recent_missing_dates(), ["2026-05-22"])

    def test_picks_up_multiple_gaps_in_the_middle(self):
        processor = _StubProcessor()
        inputs = [
            "2026-05-08",
            "2026-05-15",
            "2026-05-22",
            "2026-05-29",
            "2026-06-05",
        ]
        outputs = ["2026-05-08", "2026-05-22", "2026-06-05"]
        with (
            patch.object(processor, "gather_input_dates", return_value=inputs),
            patch.object(processor, "gather_output_dates", return_value=outputs),
        ):
            self.assertEqual(
                processor.get_most_recent_missing_dates(),
                ["2026-05-15", "2026-05-29"],
            )


if __name__ == "__main__":
    unittest.main()
