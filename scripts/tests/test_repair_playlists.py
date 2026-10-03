import unittest

from scripts.repair_playlists import (
    SKIP_VIDEO_IDS,
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

    def test_remove_placeholder(self):
        self.assertEqual(
            classify_item(CITY_COUNCIL, "Deleted video", "v3")[0], "remove"
        )
        self.assertEqual(
            classify_item(CITY_COUNCIL, "Private video", None)[0], "remove"
        )

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


if __name__ == "__main__":
    unittest.main()
