import os
import unittest
from unittest.mock import MagicMock, patch

from src.processors.tests._helpers import TempDirTestCase, make_uploader
from src.processors.upload_video import VideoUploader
from src.util import get_part_num_from_string


def _item(title, vid):
    return {"snippet": {"title": title, "resourceId": {"videoId": vid}}}


class TestGetRecentVideoTitlesPaginates(unittest.TestCase):
    """The idempotency check must see EVERY uploaded video (across all pages),
    or once the channel exceeds one page the pipeline re-uploads duplicates."""

    def test_follows_all_pages_and_filters_links_to_this_type(self):
        uploader = make_uploader(VideoUploader)  # defaults to City Council
        page1 = {
            "items": [
                _item("Clayton CA City Council Meeting 2026-06-16 07:00 PM", "cc1"),
                _item(
                    "Clayton CA Planning Commission Meeting 2026-05-26 07:00 PM", "pc"
                ),
            ]
        }
        page2 = {
            "items": [
                _item("Clayton CA City Council Meeting 2026-07-07 07:00 PM", "cc2"),
            ]
        }
        req1, req2 = MagicMock(), MagicMock()
        req1.execute.return_value = page1
        req2.execute.return_value = page2
        pi = MagicMock()
        pi.list.return_value = req1
        pi.list_next.side_effect = [req2, None]  # page2, then stop
        uploader.service = MagicMock()
        uploader.service.playlistItems.return_value = pi

        with patch.object(uploader, "update_video_links_in_redis") as upd:
            titles = uploader.get_recent_video_titles("UPLOADS")

        # Titles from BOTH pages are returned (nothing scrolls off).
        self.assertEqual(len(titles), 3)
        self.assertIn("Clayton CA City Council Meeting 2026-07-07 07:00 PM", titles)
        # Only this type's titles get their links recorded (no cross-type write).
        recorded = upd.call_args.args[0]  # {title: video_id} for this type only
        self.assertEqual(len(recorded), 2)
        self.assertIn("Clayton CA City Council Meeting 2026-06-16 07:00 PM", recorded)
        self.assertIn("Clayton CA City Council Meeting 2026-07-07 07:00 PM", recorded)
        self.assertNotIn(
            "Clayton CA Planning Commission Meeting 2026-05-26 07:00 PM", recorded
        )


class TestGetPartNumFromString(unittest.TestCase):
    def setUp(self):
        self.uploader = make_uploader(VideoUploader)

    def test_extracts_explicit_part_number(self):
        self.assertEqual(get_part_num_from_string("foo part 5 bar"), 5)

    def test_extracts_three_digit_segment_number_offset_by_one(self):
        self.assertEqual(
            get_part_num_from_string(
                "Clayton CA City Council Meeting 2026-05-08 - 002.mp4"
            ),
            3,
        )

    def test_returns_one_when_no_part_info(self):
        self.assertEqual(get_part_num_from_string("nothing relevant here"), 1)

    def test_three_digit_segment_zero_returns_one(self):
        self.assertEqual(
            get_part_num_from_string(
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


class TestProcessForDateFailsLoudly(unittest.TestCase):
    """A failed upload must NOT be reported as completed (TRA-123): it used to
    swallow the HttpError and fall through to log_complete + a 'completed'
    Discord message, so a date that hit YouTube's daily limit was marked done
    and silently skipped."""

    def test_http_error_raises_and_does_not_report_completed(self):
        from googleapiclient.errors import HttpError

        uploader = make_uploader(VideoUploader)
        uploader.service = MagicMock()  # truthy: skip re-auth
        uploader.playlists = ["existing"]  # truthy: skip get_playlists()
        date = "/vol/Clayton CA City Council Meeting 2026-05-08 07_00 PM - 000.mp4"
        err = HttpError(MagicMock(status=403), b"dailyLimitExceeded")

        with (
            patch("src.processors.upload_video.get_file_size_in_mb", return_value=999),
            patch("src.processors.upload_video.send_to_discord_bots") as discord,
            patch.object(uploader, "get_publish_datetime", return_value=""),
            patch.object(uploader, "initialize_upload", side_effect=err),
        ):
            with self.assertRaises(Exception):
                uploader.process_for_date(date)
            discord.assert_not_called()  # never falsely reports "completed"


class TestPlaylistBucketing(unittest.TestCase):
    """Videos must only resolve to THIS meeting type's playlist (TRA-155): the old
    get_playlists mapped every year-titled playlist to this type's key, so a
    non-City-Council upload landed in a City Council playlist."""

    def _uploader_with_playlists(self, items):
        uploader = make_uploader(VideoUploader)  # City Council
        uploader.service = MagicMock()
        playlists = uploader.service.playlists.return_value
        playlists.list.return_value.execute.return_value = {"items": items}
        playlists.list_next.return_value = None  # single page
        return uploader

    def test_get_playlists_records_only_this_types_playlists(self):
        items = [
            {"id": "cc26", "snippet": {"title": "2026 City Council Meetings"}},
            {"id": "pc26", "snippet": {"title": "2026 Planning Commission Meetings"}},
            {"id": "misc", "snippet": {"title": "Watch later"}},  # no year
        ]
        uploader = self._uploader_with_playlists(items)
        uploader.get_playlists()
        # Only the City Council playlist is recorded — never the PC one.
        self.assertEqual(uploader.playlists, [{"cc26": "2026"}])

    def test_get_playlist_for_year_resolves_from_type_list_else_creates(self):
        uploader = make_uploader(VideoUploader)
        uploader.playlists = [{"cc26": "2026"}, {"cc25": "2025"}]
        # A year present in this type's list resolves without creating.
        with patch.object(uploader, "create_playlist_for_year") as create:
            self.assertEqual(uploader.get_playlist_for_year("2026"), "cc26")
        create.assert_not_called()
        # A year not in the list creates the type's own playlist (a stale pointer
        # for another type is never consulted — the resolver doesn't read Redis).
        with patch.object(
            uploader, "create_playlist_for_year", return_value="new24"
        ) as create:
            self.assertEqual(uploader.get_playlist_for_year("2024"), "new24")
        create.assert_called_once_with("2024")


if __name__ == "__main__":
    unittest.main()
