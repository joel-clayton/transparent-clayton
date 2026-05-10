import os
import unittest
from unittest.mock import patch

from src.processors.tests._helpers import TempDirTestCase, make_uploader
from src.processors.upload_video import VideoUploader


class TestGetPartNumFromString(unittest.TestCase):
    def setUp(self):
        self.uploader = make_uploader(VideoUploader)

    def test_extracts_explicit_part_number(self):
        self.assertEqual(self.uploader.get_part_num_from_string("foo part 5 bar"), 5)

    def test_extracts_three_digit_segment_number_offset_by_one(self):
        self.assertEqual(
            self.uploader.get_part_num_from_string(
                "Clayton CA City Council Meeting 2026-05-08 - 002.mp4"
            ),
            3,
        )

    def test_returns_one_when_no_part_info(self):
        self.assertEqual(
            self.uploader.get_part_num_from_string("nothing relevant here"), 1
        )

    def test_three_digit_segment_zero_returns_one(self):
        self.assertEqual(
            self.uploader.get_part_num_from_string(
                "Clayton CA City Council Meeting 2026-05-08 - 000.mp4"
            ),
            1,
        )


class TestGetOutputTitleFromInput(unittest.TestCase):
    def setUp(self):
        self.uploader = make_uploader(VideoUploader)

    def test_datetime_input_produces_datetime_title(self):
        title = self.uploader.get_output_title_from_input(
            "/path/Clayton CA City Council Meeting 2026-05-08 07_00 PM - 000.mp4"
        )
        self.assertEqual(title, "Clayton CA City Council Meeting 2026-05-08 07:00 PM")

    def test_date_input_produces_date_title(self):
        title = self.uploader.get_output_title_from_input(
            "/path/Clayton CA City Council Meeting 2026-05-08 - 000.mp4"
        )
        self.assertEqual(title, "Clayton CA City Council Meeting 2026-05-08")

    def test_appends_part_suffix_for_segment_after_first(self):
        title = self.uploader.get_output_title_from_input(
            "/path/Clayton CA City Council Meeting 2026-05-08 - 002.mp4"
        )
        self.assertEqual(title, "Clayton CA City Council Meeting 2026-05-08 part 3")


class TestVideoUploaderGatherDates(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.uploader = make_uploader(VideoUploader)

    def test_returns_absolute_paths_matching_compressed_pattern(self):
        self.touch("Clayton CA City Council Meeting 2026-05-08 - 000.mp4")
        self.touch("Clayton CA City Council Meeting 2026-06-01 - 000.mp4")
        result = self.uploader.gather_dates(self.tmpdir)
        self.assertEqual(len(result), 2)
        self.assertTrue(all(os.path.isabs(p) for p in result))
        self.assertTrue(any("2026-05-08" in p for p in result))
        self.assertTrue(any("2026-06-01" in p for p in result))

    def test_skips_files_not_matching_compressed_pattern(self):
        self.touch("Random Other File.mp4")
        self.assertEqual(self.uploader.gather_dates(self.tmpdir), [])


class TestGetMostRecentMissingDates(unittest.TestCase):
    def setUp(self):
        self.uploader = make_uploader(VideoUploader)

    def _patch_gather(self, inputs, outputs):
        return (
            patch.object(self.uploader, "gather_input_dates", return_value=inputs),
            patch.object(self.uploader, "gather_output_dates", return_value=outputs),
        )

    def test_date_only_inputs_dont_collapse_to_same_key(self):
        inputs = [
            "/x/Clayton CA City Council Meeting 2026-05-08 - 000.mp4",
            "/x/Clayton CA City Council Meeting 2026-06-01 - 000.mp4",
        ]
        p_in, p_out = self._patch_gather(inputs, [])
        with p_in, p_out:
            missing = self.uploader.get_most_recent_missing_dates()
        self.assertEqual(len(missing), 2)

    def test_excludes_already_uploaded(self):
        inputs = [
            "/x/Clayton CA City Council Meeting 2026-05-08 - 000.mp4",
            "/x/Clayton CA City Council Meeting 2026-06-01 - 000.mp4",
        ]
        outputs = ["Clayton CA City Council Meeting 2026-05-08"]
        p_in, p_out = self._patch_gather(inputs, outputs)
        with p_in, p_out:
            missing = self.uploader.get_most_recent_missing_dates()
        self.assertEqual(len(missing), 1)
        self.assertIn("2026-06-01", missing[0])

    def test_datetime_inputs_keyed_separately(self):
        inputs = [
            "/x/Clayton CA City Council Meeting 2026-05-08 07_00 PM - 000.mp4",
            "/x/Clayton CA City Council Meeting 2026-05-08 - 000.mp4",
        ]
        p_in, p_out = self._patch_gather(inputs, [])
        with p_in, p_out:
            missing = self.uploader.get_most_recent_missing_dates()
        self.assertEqual(len(missing), 2)


if __name__ == "__main__":
    unittest.main()
