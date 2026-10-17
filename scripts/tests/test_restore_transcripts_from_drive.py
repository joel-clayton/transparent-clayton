import unittest

from scripts import restore_transcripts_from_drive as rt
from src.meeting_types import CITY_COUNCIL


class TestRestoreHelpers(unittest.TestCase):
    def test_meeting_key_from_folder_colon_datetime_to_underscore(self):
        self.assertEqual(
            rt.meeting_key_from_folder("City Council Meeting 2016-10-04 07:00 PM"),
            "2016-10-04 07_00 PM",
        )

    def test_meeting_key_from_folder_date_only(self):
        self.assertEqual(
            rt.meeting_key_from_folder("City Council Meeting 2010-03-02"),
            "2010-03-02",
        )

    def test_meeting_key_from_folder_unparseable(self):
        self.assertIsNone(rt.meeting_key_from_folder("Some Folder"))

    def test_transcript_disk_path_matches_pipeline_naming(self):
        self.assertEqual(
            rt.transcript_disk_path("/root", CITY_COUNCIL, "2016-10-04 07_00 PM"),
            "/root/City Council/City Council Meeting "
            "2016-10-04 07_00 PM - City of Clayton.txt",
        )


if __name__ == "__main__":
    unittest.main()
