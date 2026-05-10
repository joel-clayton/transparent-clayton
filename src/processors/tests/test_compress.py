import unittest
from unittest.mock import patch

from src.processors.compress import Compressor
from src.processors.tests._helpers import TempDirTestCase


class TestCompressorGatherDates(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.compressor = Compressor()
        self.input_tmp = self.tmpdir
        self.output_tmp = self.make_tmpdir()

    def test_gather_input_reads_from_downloaded_dir(self):
        self.touch(
            "City Council Meeting 2026-05-08 - City of Clayton.mp4", self.input_tmp
        )
        with patch("src.processors.compress.DOWNLOADED_DIR", self.input_tmp):
            self.assertEqual(self.compressor.gather_input_dates(), ["2026-05-08"])

    def test_gather_output_reads_from_compressed_dir(self):
        self.touch(
            "City Council Meeting 2026-05-08 - City of Clayton.mp4", self.output_tmp
        )
        with patch("src.processors.compress.COMPRESSED_DIR", self.output_tmp):
            self.assertEqual(self.compressor.gather_output_dates(), ["2026-05-08"])

    def test_handles_mixed_date_and_datetime_filenames(self):
        self.touch(
            "City Council Meeting 2026-05-08 - City of Clayton.mp4", self.input_tmp
        )
        self.touch(
            "City Council Meeting 2026-06-01 07_00 PM - City of Clayton.mp4",
            self.input_tmp,
        )
        with patch("src.processors.compress.DOWNLOADED_DIR", self.input_tmp):
            self.assertEqual(
                self.compressor.gather_input_dates(),
                ["2026-05-08", "2026-06-01 07_00 PM"],
            )


if __name__ == "__main__":
    unittest.main()
