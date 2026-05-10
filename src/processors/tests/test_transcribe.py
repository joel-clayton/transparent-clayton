import unittest
from unittest.mock import patch

from src.processors.transcribe import Transcriber
from src.processors.tests._helpers import TempDirTestCase


class TestTranscriberGatherDates(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.transcriber = Transcriber()
        self.input_tmp = self.tmpdir
        self.output_tmp = self.make_tmpdir()

    def test_gather_input_reads_extracted_audio_dir(self):
        self.touch(
            "City Council Meeting 2026-05-08 - City of Clayton.m4a", self.input_tmp
        )
        with patch("src.processors.transcribe.EXTRACTED_AUDIO_DIR", self.input_tmp):
            self.assertEqual(self.transcriber.gather_input_dates(), ["2026-05-08"])

    def test_gather_output_reads_transcribed_dir(self):
        self.touch(
            "City Council Meeting 2026-05-08 - City of Clayton.txt", self.output_tmp
        )
        with patch("src.processors.transcribe.TRANSCRIBED_DIR", self.output_tmp):
            self.assertEqual(self.transcriber.gather_output_dates(), ["2026-05-08"])

    def test_handles_mixed_date_and_datetime(self):
        self.touch(
            "City Council Meeting 2026-05-08 - City of Clayton.m4a", self.input_tmp
        )
        self.touch(
            "City Council Meeting 2026-06-01 07_00 PM - City of Clayton.m4a",
            self.input_tmp,
        )
        with patch("src.processors.transcribe.EXTRACTED_AUDIO_DIR", self.input_tmp):
            self.assertEqual(
                self.transcriber.gather_input_dates(),
                ["2026-05-08", "2026-06-01 07_00 PM"],
            )


class TestTranscriberCutoffSkip(unittest.TestCase):
    def setUp(self):
        self.transcriber = Transcriber()

    def test_skips_date_before_cutoff(self):
        with patch("src.processors.transcribe.requests.post") as mock_post:
            self.assertIsNone(self.transcriber.process_for_date("2024-01-01"))
            mock_post.assert_not_called()

    def test_skips_datetime_before_cutoff(self):
        with patch("src.processors.transcribe.requests.post") as mock_post:
            self.assertIsNone(self.transcriber.process_for_date("2024-01-01 07_00 PM"))
            mock_post.assert_not_called()

    def test_raises_when_string_has_no_parseable_date(self):
        with patch("src.processors.transcribe.requests.post") as mock_post:
            with self.assertRaises(Exception):
                self.transcriber.process_for_date("nothing parseable")
            mock_post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
