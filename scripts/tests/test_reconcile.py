import os
import tempfile
import unittest
from unittest.mock import MagicMock

from googleapiclient.errors import HttpError

from scripts.reconcile import (
    MeetingRow,
    Scope,
    TypeReport,
    _drive_file_id,
    _has_presence,
    _is_stale_drive_link,
    _iter_stage_filenames,
    _key_datetime,
    _meeting_key_from_filename,
    _video_id_from_link,
    find_mismatches,
    live_drive_ids,
)
from src.meeting_types import CITY_COUNCIL

ALL = Scope.of(youtube=True, google_docs=True, wiki=True)


def _fm(rows, scope=ALL, channel_ids=frozenset(), drive_ids=frozenset()):
    report = TypeReport(meeting_type=CITY_COUNCIL, rows=rows)
    return find_mismatches(
        report,
        channel_ids=set(channel_ids),
        drive_ids=set(drive_ids),
        include_historical=False,
        scope=scope,
    )


class TestPureHelpers(unittest.TestCase):
    def test_video_id_from_link(self):
        self.assertEqual(
            _video_id_from_link("https://www.youtube.com/watch?v=abc123"), "abc123"
        )
        self.assertEqual(_video_id_from_link("abc123"), "abc123")

    def test_drive_file_id_from_docs_and_file_links(self):
        self.assertEqual(
            _drive_file_id("https://docs.google.com/document/d/DOC123/edit?usp=x"),
            "DOC123",
        )
        self.assertEqual(
            _drive_file_id("https://drive.google.com/file/d/FILE_9-a/view"), "FILE_9-a"
        )
        self.assertIsNone(_drive_file_id("https://example.com/nope"))

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
        # Date-only fallback (DATE_PATTERN) — used for date-keyed meetings.
        self.assertEqual(
            _meeting_key_from_filename("City Council Meeting 2026-05-08.txt"),
            "2026-05-08",
        )
        self.assertIsNone(_meeting_key_from_filename("Budget Committee 05-21-24.m4a"))

    def test_has_presence(self):
        self.assertTrue(_has_presence(MeetingRow(key="k", has_detail=True)))
        self.assertTrue(_has_presence(MeetingRow(key="k", on_disk={"compressed"})))
        self.assertTrue(_has_presence(MeetingRow(key="k", docs_on_disk=True)))
        self.assertFalse(_has_presence(MeetingRow(key="k", link_parts={1: "x"})))


class TestVideoFindings(unittest.TestCase):
    def test_stale_link_on_real_meeting_is_flagged(self):
        rows = {
            "2026-06-02 07_00 PM": MeetingRow(
                key="2026-06-02 07_00 PM", has_detail=True, link_parts={2: "DEAD"}
            )
        }
        lines, orphans, historical = _fm(rows)
        self.assertIn("STALE_VIDEO_LINK part 2->DEAD", lines[0])
        self.assertEqual((orphans, historical), (0, 0))

    def test_orphan_link_without_presence_is_counted_not_listed(self):
        rows = {"k": MeetingRow(key="2026-07-01 07_00 PM", link_parts={1: "DEAD"})}
        lines, orphans, _ = _fm(rows)
        self.assertEqual((lines, orphans), ([], 1))

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
        lines, _, _ = _fm(rows, channel_ids={"live"})
        self.assertIn("MISSING_UPLOAD parts 2", lines[0])

    def test_historical_meeting_skipped_unless_included(self):
        rows = {
            "2020-01-01 07_00 PM": MeetingRow(
                key="2020-01-01 07_00 PM", has_detail=True, link_parts={1: "DEAD"}
            )
        }
        report = TypeReport(meeting_type=CITY_COUNCIL, rows=rows)
        lines, _, historical = find_mismatches(
            report, set(), set(), include_historical=False, scope=ALL
        )
        self.assertEqual((lines, historical), ([], 1))
        lines2, _, _ = find_mismatches(
            report, set(), set(), include_historical=True, scope=ALL
        )
        self.assertEqual(len(lines2), 1)


class TestDriveFindings(unittest.TestCase):
    KEY = "2026-06-02 07_00 PM"
    T_LIVE = "https://docs.google.com/document/d/LIVE/edit"
    T_DEAD = "https://docs.google.com/document/d/DEAD/edit"
    D_DEAD = "https://drive.google.com/file/d/DOCDEAD/view"

    def test_missing_transcript_when_on_disk_without_link(self):
        rows = {
            self.KEY: MeetingRow(key=self.KEY, has_detail=True, on_disk={"transcript"})
        }
        lines, _, _ = _fm(rows)
        self.assertIn("MISSING_TRANSCRIPT", lines[0])

    def test_stale_transcript_link_when_drive_file_gone(self):
        rows = {
            self.KEY: MeetingRow(
                key=self.KEY, has_detail=True, transcript_link=self.T_DEAD
            )
        }
        # Not in drive_ids -> stale.
        self.assertIn("STALE_TRANSCRIPT_LINK", _fm(rows)[0][0])
        # Live -> clean.
        self.assertEqual(_fm(rows, drive_ids={"DEAD"})[0], [])

    def test_stale_doc_link_lists_dead_label(self):
        rows = {
            self.KEY: MeetingRow(
                key=self.KEY, has_detail=True, doc_links={"Staff Report": self.D_DEAD}
            )
        }
        lines, _, _ = _fm(rows)
        self.assertIn("STALE_DOC_LINK Staff Report", lines[0])

    def test_missing_doc_when_on_disk_without_archive(self):
        rows = {self.KEY: MeetingRow(key=self.KEY, has_detail=True, docs_on_disk=True)}
        lines, _, _ = _fm(rows)
        self.assertIn("MISSING_DOC", lines[0])


class TestDriveLiveness(unittest.TestCase):
    def test_is_stale_only_for_parseable_confirmed_dead(self):
        dead = "https://docs.google.com/document/d/DEAD/edit"
        live = "https://docs.google.com/document/d/LIVE/edit"
        folder = "https://drive.google.com/drive/folders/X"  # no /d/<id>
        self.assertTrue(_is_stale_drive_link(dead, set()))
        self.assertFalse(_is_stale_drive_link(live, {"LIVE"}))
        # Unparseable link can't be verified -> never stale (never deleted).
        self.assertFalse(_is_stale_drive_link(folder, set()))

    def test_only_404_and_trashed_count_as_dead(self):
        outcomes = {"live": "ok", "trash": "trashed", "gone": 404, "blip": 500}

        def get(fileId, **kwargs):
            req = MagicMock()
            outcome = outcomes[fileId]
            if outcome == "ok":
                req.execute.return_value = {"id": fileId, "trashed": False}
            elif outcome == "trashed":
                req.execute.return_value = {"id": fileId, "trashed": True}
            else:
                req.execute.side_effect = HttpError(MagicMock(status=outcome), b"")
            return req

        service = MagicMock()
        service.files.return_value.get.side_effect = get
        result = live_drive_ids(service, set(outcomes))
        # 404 and trashed are dead; a transient 500 is treated as live (never
        # deleted on uncertainty).
        self.assertEqual(result, {"live", "blip"})


class TestIterStageFilenames(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.stage = td.name

    def _touch(self, *parts):
        path = os.path.join(self.stage, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w"):
            pass

    def test_reads_flat_and_bucket_but_ignores_non_bucket_subdirs(self):
        self._touch("flat.mp4")  # legacy flat file
        self._touch("City Council", "bucketed.mp4")  # a real per-type bucket
        self._touch(".tmp_ffmpeg", "part.mp4")  # a stray non-bucket subdir
        found = set(_iter_stage_filenames(self.stage))
        # The stray subdir's contents are NOT counted; only flat + known buckets.
        self.assertEqual(found, {"flat.mp4", "bucketed.mp4"})


class TestScoping(unittest.TestCase):
    KEY = "2026-06-02 07_00 PM"

    def test_youtube_disabled_hides_video_findings_and_orphans(self):
        rows = {
            self.KEY: MeetingRow(key=self.KEY, has_detail=True, link_parts={2: "DEAD"}),
            "orphan": MeetingRow(key="2026-07-01 07_00 PM", link_parts={1: "DEAD"}),
        }
        lines, orphans, _ = _fm(
            rows, scope=Scope.of(youtube=False, google_docs=True, wiki=True)
        )
        self.assertEqual((lines, orphans), ([], 0))

    def test_google_docs_disabled_hides_drive_findings(self):
        rows = {
            self.KEY: MeetingRow(
                key=self.KEY,
                has_detail=True,
                on_disk={"transcript"},
                docs_on_disk=True,
                transcript_link="https://docs.google.com/document/d/DEAD/edit",
            )
        }
        self.assertTrue(
            _fm(rows, scope=Scope.of(youtube=True, google_docs=True, wiki=True))[0]
        )  # findings present
        self.assertEqual(
            _fm(rows, scope=Scope.of(youtube=True, google_docs=False, wiki=True))[0], []
        )  # hidden


class TestScope(unittest.TestCase):
    def test_from_config_covers_every_registry_destination(self):
        # The scoped destination set is derived from the publisher registry, so a
        # new/renamed destination is never silently left un-scoped.
        from src.publishers import PUBLISHERS

        scope = Scope.from_config()
        self.assertEqual(
            set(scope.enabled), {p.destination for p in PUBLISHERS.values()}
        )

    def test_summary_partitions_in_a_single_pass(self):
        summary = Scope.of(youtube=True, google_docs=False, wiki=True).summary()
        self.assertEqual(summary, "in scope: youtube, wiki; disabled: google_docs")

    def test_known_but_disabled_destination_returns_false(self):
        self.assertFalse(Scope.of(youtube=False, google_docs=True, wiki=True).youtube)

    def test_accessor_for_unregistered_destination_raises(self):
        # A registry rename that leaves a named accessor dangling must fail loudly,
        # not silently disable that destination's findings.
        scope = Scope.of(video=True, google_docs=True, wiki=True)  # 'youtube' renamed
        with self.assertRaises(KeyError):
            _ = scope.youtube


if __name__ == "__main__":
    unittest.main()
