import unittest
from unittest.mock import MagicMock, patch

from googleapiclient.errors import HttpError

from scripts.repair_playlists import (
    SKIP_VIDEO_IDS,
    _all_playlists,
    _ensure_playlist,
    _insert_video,
    _perform_move,
    _playlist_items,
    classify_item,
    owner_type,
    video_type,
)
from src.meeting_types import CITY_COUNCIL, GENERAL, PLANNING_COMMISSION


class TestOwnerAndVideoType(unittest.TestCase):
    def test_owner_type_from_playlist_title(self):
        self.assertEqual(owner_type("2026 City Council Meetings"), CITY_COUNCIL)
        self.assertEqual(
            owner_type("2026 Planning Commission Meetings"), PLANNING_COMMISSION
        )
        self.assertIsNone(owner_type("Watch later"))  # no year / not managed
        self.assertIsNone(owner_type("2026 Random Playlist"))

    def test_video_type_from_title_prefix(self):
        self.assertEqual(
            video_type("Clayton CA City Council Meeting 2026-06-02 07:00 PM"),
            CITY_COUNCIL,
        )
        self.assertEqual(
            video_type("Clayton CA GHAD Meeting 2026-07-07 06:30 PM").key, "ghad_mtg"
        )
        self.assertIsNone(video_type("Some unrelated video"))


class TestClassifyItem(unittest.TestCase):
    def test_keep_when_video_matches_owner(self):
        action, dest = classify_item(
            CITY_COUNCIL, "Clayton CA City Council Meeting 2026-06-02 07:00 PM", "v1"
        )
        self.assertEqual((action, dest), ("keep", None))

    def test_keep_when_type_unknown(self):
        self.assertEqual(classify_item(CITY_COUNCIL, "Mystery clip", "v2")[0], "keep")

    def test_remove_only_truly_dead_placeholder(self):
        self.assertEqual(
            classify_item(CITY_COUNCIL, "Deleted video", "v3")[0], "remove"
        )
        # A "Private video" is live (hidden), not dead — never delete its entry.
        self.assertEqual(classify_item(CITY_COUNCIL, "Private video", None)[0], "keep")

    def test_move_when_misfiled(self):
        action, dest = classify_item(
            CITY_COUNCIL,
            "Clayton CA Planning Commission Meeting 2026-05-26 07:00 PM",
            "vpc",
        )
        self.assertEqual(action, "move")
        self.assertEqual(dest, PLANNING_COMMISSION)

    def test_skip_listed_obsolete_video(self):
        vid = next(iter(SKIP_VIDEO_IDS))
        # Even though it's misfiled (a General video in a CC playlist), a skip-
        # listed id is left for manual deletion rather than moved.
        action, dest = classify_item(
            CITY_COUNCIL, "Clayton CA General Meeting 2026-05-26 07:00 PM", vid
        )
        self.assertEqual((action, dest), ("skip", None))
        # A non-skip-listed General video misfiled in a CC playlist DOES move.
        self.assertEqual(
            classify_item(
                CITY_COUNCIL, "Clayton CA General Meeting 2026-08-26 06:00 PM", "ok"
            ),
            ("move", GENERAL),
        )


class TestPlaylistHelpers(unittest.TestCase):
    def test_ensure_playlist_reports_created_then_cached(self):
        svc = MagicMock()
        svc.playlists.return_value.insert.return_value.execute.return_value = {
            "id": "NEW"
        }
        cache: dict[str, str] = {}
        self.assertEqual(_ensure_playlist(svc, "2026 X Meetings", cache), ("NEW", True))
        svc.playlists.return_value.insert.reset_mock()
        # Second call: cached, reported as not-created, and no insert issued.
        self.assertEqual(
            _ensure_playlist(svc, "2026 X Meetings", cache), ("NEW", False)
        )
        svc.playlists.return_value.insert.assert_not_called()

    def test_perform_move_into_created_playlist_does_not_list_it(self):
        """The TRA-157 regression: a just-created playlist must NOT be listed to
        seed the dedup set (listing one that hasn't propagated 404s)."""
        svc = MagicMock()
        svc.playlists.return_value.insert.return_value.execute.return_value = {
            "id": "NEWID"
        }
        name_to_id: dict[str, str] = {}  # dest absent -> _ensure_playlist creates it
        dest_members: dict[str, set[str]] = {}
        item = {"video_id": "vid", "item_id": "itemid", "title": "t"}

        _perform_move(svc, item, "2026 X Meetings", name_to_id, dest_members, set())

        svc.playlistItems.return_value.list.assert_not_called()  # never lists the new one
        svc.playlistItems.return_value.insert.assert_called_once()
        svc.playlistItems.return_value.delete.assert_called_once_with(id="itemid")
        self.assertEqual(dest_members["NEWID"], {"vid"})

    def test_perform_move_existing_playlist_dedups_but_still_removes_source(self):
        svc = MagicMock()
        name_to_id = {"2026 X Meetings": "EXIST"}  # present -> created=False, listed
        dest_members: dict[str, set[str]] = {}
        svc.playlistItems.return_value.list.return_value.execute.return_value = {
            "items": [
                {
                    "id": "i",
                    "snippet": {"title": "t", "resourceId": {"videoId": "vid"}},
                }
            ]
        }
        svc.playlistItems.return_value.list_next.return_value = None
        item = {"video_id": "vid", "item_id": "itemid", "title": "t"}

        _perform_move(svc, item, "2026 X Meetings", name_to_id, dest_members, set())

        # Already in the destination -> no second insert, but still removed from source.
        svc.playlistItems.return_value.insert.assert_not_called()
        svc.playlistItems.return_value.delete.assert_called_once_with(id="itemid")


class TestInsertVideoRetry(unittest.TestCase):
    def test_just_created_retries_404_then_succeeds(self):
        svc = MagicMock()
        execute = svc.playlistItems.return_value.insert.return_value.execute
        execute.side_effect = [HttpError(MagicMock(status=404), b"not found"), None]
        with patch("scripts.repair_playlists.time.sleep"):
            _insert_video(svc, "PID", "vid", retry_transient=True)
        self.assertEqual(execute.call_count, 2)  # retried once, then succeeded

    def test_just_created_retries_transient_5xx(self):
        svc = MagicMock()
        execute = svc.playlistItems.return_value.insert.return_value.execute
        execute.side_effect = [HttpError(MagicMock(status=503), b"busy"), None]
        with patch("scripts.repair_playlists.time.sleep"):
            _insert_video(svc, "PID", "vid", retry_transient=True)
        self.assertEqual(execute.call_count, 2)

    def test_preexisting_playlist_fails_fast_on_404(self):
        # A pre-existing (not just-created) playlist that 404s is genuinely gone;
        # surface it immediately instead of wasting retries.
        svc = MagicMock()
        execute = svc.playlistItems.return_value.insert.return_value.execute
        execute.side_effect = HttpError(MagicMock(status=404), b"gone")
        with self.assertRaises(HttpError):
            _insert_video(svc, "PID", "vid", retry_transient=False)
        self.assertEqual(execute.call_count, 1)  # no retries

    def test_reraises_a_non_retriable_status(self):
        svc = MagicMock()
        svc.playlistItems.return_value.insert.return_value.execute.side_effect = (
            HttpError(MagicMock(status=403), b"forbidden")
        )
        with self.assertRaises(HttpError):
            _insert_video(svc, "PID", "vid", retry_transient=True)


class TestPaginationCapRaises(unittest.TestCase):
    """A truncated listing can't drive a safe repair (wrong moves / deleting valid
    pointers), so hitting the page cap raises rather than proceeding."""

    def test_all_playlists_raises_when_cap_hit(self):
        svc = MagicMock()
        svc.playlists.return_value.list.return_value.execute.return_value = {
            "items": []
        }
        svc.playlists.return_value.list_next.return_value = MagicMock()  # always more
        with patch("scripts.repair_playlists.MAX_UPLOADS_PAGES", 1):
            with self.assertRaises(RuntimeError):
                _all_playlists(svc)

    def test_playlist_items_raises_when_cap_hit(self):
        svc = MagicMock()
        svc.playlistItems.return_value.list.return_value.execute.return_value = {
            "items": []
        }
        svc.playlistItems.return_value.list_next.return_value = MagicMock()  # more
        with patch("scripts.repair_playlists.MAX_UPLOADS_PAGES", 1):
            with self.assertRaises(RuntimeError):
                _playlist_items(svc, "PID")


if __name__ == "__main__":
    unittest.main()
