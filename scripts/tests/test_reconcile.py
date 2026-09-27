import unittest

from scripts.reconcile import (
    MeetingRow,
    TypeReport,
    _has_presence,
    _key_datetime,
    _meeting_key_from_filename,
    _video_id_from_link,
    find_mismatches,
)
from src.meeting_types import CITY_COUNCIL


class TestPureHelpers(unittest.TestCase):
    def test_video_id_from_link(self):
        self.assertEqual(
            _video_id_from_link("https://www.youtube.com/watch?v=abc123"), "abc123"
        )
        self.assertEqual(_video_id_from_link("abc123"), "abc123")

    def test_key_datetime_parses_both_shapes(self):
        self.assertIsNotNone(_key_datetime("2026-05-26 07_00 PM"))
        self.assertIsNotNone(_key_datetime("2026-05-26"))
        self.assertIsNone(_key_datetime("not a date"))

    def test_meeting_key_from_filename(self):
        self.assertEqual(
            _meeting_key_from_filename(
                "Clayton CA City Council Meeting 2026-05-26 07_00 PM - 000.mp4"
            ),
            "2026-05-26 07_00 PM",
        )
        self.assertEqual(
            _meeting_key_from_filename("City Council Meeting 2026-05-08.txt"),
            "2026-05-08",
        )
        self.assertIsNone(_meeting_key_from_filename("Budget Committee 05-21-24.m4a"))

    def test_has_presence(self):
        self.assertTrue(_has_presence(MeetingRow(key="k", has_detail=True)))
        self.assertTrue(_has_presence(MeetingRow(key="k", on_disk={"compressed"})))
        self.assertFalse(_has_presence(MeetingRow(key="k", link_parts={1: "x"})))


class TestFindMismatches(unittest.TestCase):
    def _report(self, rows):
        return TypeReport(meeting_type=CITY_COUNCIL, rows=rows)

    def test_stale_link_on_real_meeting_is_flagged(self):
        rows = {
            "2026-06-02 07_00 PM": MeetingRow(
                key="2026-06-02 07_00 PM", has_detail=True, link_parts={2: "DEAD"}
            )
        }
        lines, orphans, historical = find_mismatches(
            self._report(rows), channel_ids=set(), include_historical=False
        )
        self.assertEqual(len(lines), 1)
        self.assertIn("STALE_VIDEO_LINK part 2->DEAD", lines[0])
        self.assertEqual((orphans, historical), (0, 0))

    def test_orphan_link_without_presence_is_counted_not_listed(self):
        rows = {
            "2026-07-01 07_00 PM": MeetingRow(
                key="2026-07-01 07_00 PM", link_parts={1: "DEAD"}
            )
        }
        lines, orphans, historical = find_mismatches(
            self._report(rows), channel_ids=set(), include_historical=False
        )
        self.assertEqual(lines, [])
        self.assertEqual(orphans, 1)

    def test_missing_upload_when_disk_part_not_on_youtube(self):
        rows = {
            "2026-06-02 07_00 PM": MeetingRow(
                key="2026-06-02 07_00 PM",
                has_detail=True,
                disk_parts={1, 2},
                yt_parts={1: "live"},
                link_parts={1: "live"},
            )
        }
        lines, _, _ = find_mismatches(
            self._report(rows), channel_ids={"live"}, include_historical=False
        )
        self.assertEqual(len(lines), 1)
        self.assertIn("MISSING_UPLOAD parts 2", lines[0])

    def test_historical_meeting_skipped_unless_included(self):
        rows = {
            "2020-01-01 07_00 PM": MeetingRow(
                key="2020-01-01 07_00 PM", has_detail=True, link_parts={1: "DEAD"}
            )
        }
        lines, _, historical = find_mismatches(
            self._report(rows), channel_ids=set(), include_historical=False
        )
        self.assertEqual((lines, historical), ([], 1))

        lines2, _, historical2 = find_mismatches(
            self._report(rows), channel_ids=set(), include_historical=True
        )
        self.assertEqual(len(lines2), 1)
        self.assertEqual(historical2, 0)


if __name__ == "__main__":
    unittest.main()
