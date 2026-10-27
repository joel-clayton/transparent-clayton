import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from scripts import canonicalize_keys as ck
from src.meeting_types import BUDGET_AND_AUDIT, CITY_COUNCIL, GHAD


class TestFetchResilient(unittest.TestCase):
    def test_retries_then_succeeds(self):
        with (
            patch.object(
                ck, "fetch_events", side_effect=[TimeoutError("boom"), [{"ok": 1}]]
            ) as fe,
            patch.object(ck.time, "sleep") as sleep,
        ):
            out = ck.fetch_events_resilient(datetime(2000, 1, 1), datetime(2026, 1, 1))
        self.assertEqual(out, [{"ok": 1}])
        self.assertEqual(fe.call_count, 2)
        sleep.assert_called_once()

    def test_reraises_after_exhausting_attempts(self):
        with (
            patch.object(ck, "fetch_events", side_effect=TimeoutError("boom")),
            patch.object(ck.time, "sleep"),
        ):
            with self.assertRaises(TimeoutError):
                ck.fetch_events_resilient(
                    datetime(2000, 1, 1), datetime(2026, 1, 1), attempts=3
                )


def ev(dt_iso: str, category: str, name: str = "") -> dict:
    return {
        "startDateTime": dt_iso,
        "categoryName": category,
        "eventName": name,
        "publishedFiles": [],
    }


class TestClassify(unittest.TestCase):
    def test_clean_single_same_type(self):
        p = ck.classify(
            "City Council", "2025-08-11", [ev("2025-08-11T19:00:00Z", "City Council")]
        )
        self.assertEqual(p.classification, ck.CLEAN)
        self.assertEqual(p.new_bucket, "City Council")
        self.assertEqual(p.new_key, "2025-08-11 07_00 PM")
        self.assertEqual(p.new_type_key, "cc_mtg")

    def test_split_events_same_datetime_collapse_to_clean(self):
        # The portal splits one meeting across two same-time records; merge_by_datetime
        # unions them, so this is one meeting, not an ambiguous pair.
        p = ck.classify(
            "City Council",
            "2025-08-11",
            [
                ev("2025-08-11T19:00:00Z", "City Council"),
                ev("2025-08-11T19:00:00Z", "City Council"),
            ],
        )
        self.assertEqual(p.classification, ck.CLEAN)
        self.assertEqual(p.new_key, "2025-08-11 07_00 PM")

    def test_two_same_type_different_times_is_ambiguous(self):
        p = ck.classify(
            "City Council",
            "2025-08-11",
            [
                ev("2025-08-11T16:00:00Z", "City Council"),
                ev("2025-08-11T19:00:00Z", "City Council"),
            ],
        )
        self.assertEqual(p.classification, ck.AMBIGUOUS)

    def test_rebucket_single_other_type(self):
        p = ck.classify(
            "City Council",
            "2024-05-30",
            [ev("2024-05-30T16:00:00Z", "Budget and Audit Committee")],
        )
        self.assertEqual(p.classification, ck.REBUCKET)
        self.assertEqual(p.new_bucket, "Budget and Audit Committee")
        self.assertEqual(p.new_key, "2024-05-30 04_00 PM")
        self.assertEqual(p.new_type_key, "bac_mtg")

    def test_override_resolves_ambiguous_to_rebucket(self):
        # 2021-06-08 has both a B&A (4pm) and a PC (7pm) meeting, so OData alone is
        # AMBIGUOUS; the content-confirmed OVERRIDE picks B&A. Validated against the
        # events (the named target meeting must exist that day).
        p = ck.classify(
            "City Council",
            "2021-06-08",
            [
                ev("2021-06-08T16:00:00Z", "Budget and Audit Committee"),
                ev("2021-06-08T19:00:00Z", "Planning Commission"),
            ],
        )
        self.assertEqual(p.classification, ck.REBUCKET)
        self.assertEqual(p.new_bucket, "Budget and Audit Committee")
        self.assertEqual(p.new_key, "2021-06-08 04_00 PM")
        self.assertEqual(p.new_type_key, "bac_mtg")

    def test_stale_override_ignored_and_falls_through(self):
        # An override whose target meeting is absent from OData that day is ignored
        # (warned), falling back to auto-classification — here NOMATCH.
        with patch.dict(
            ck.OVERRIDES,
            {
                ("City Council", "2099-01-01"): (
                    "Budget and Audit Committee",
                    "2099-01-01 04_00 PM",
                )
            },
        ):
            p = ck.classify("City Council", "2099-01-01", [])
        self.assertEqual(p.classification, ck.NOMATCH)

    def test_multiple_candidate_types_is_ambiguous(self):
        # A neutral date (not in OVERRIDES) so this exercises the generic
        # auto-classification path rather than a content-confirmed override.
        p = ck.classify(
            "City Council",
            "2020-03-04",
            [
                ev("2020-03-04T16:00:00Z", "Budget and Audit Committee"),
                ev("2020-03-04T19:00:00Z", "Planning Commission"),
            ],
        )
        self.assertEqual(p.classification, ck.AMBIGUOUS)
        self.assertIn("Budget and Audit Committee", p.note)
        self.assertIn("Planning Commission", p.note)

    def test_no_events_is_nomatch(self):
        p = ck.classify("City Council", "2018-09-24", [])
        self.assertEqual(p.classification, ck.NOMATCH)


class TestMarkCollisions(unittest.TestCase):
    def test_two_plans_to_same_target_both_become_collision(self):
        clean = ck.Plan(
            "Budget and Audit Committee",
            "2024-05-30",
            ck.CLEAN,
            "Budget and Audit Committee",
            "2024-05-30 04_00 PM",
            "bac_mtg",
        )
        rebucket = ck.Plan(
            "City Council",
            "2024-05-30",
            ck.REBUCKET,
            "Budget and Audit Committee",
            "2024-05-30 04_00 PM",
            "bac_mtg",
        )
        lone = ck.Plan(
            "City Council",
            "2025-08-11",
            ck.CLEAN,
            "City Council",
            "2025-08-11 07_00 PM",
            "cc_mtg",
        )
        out = {
            (p.src_bucket, p.date): p.classification
            for p in ck.mark_collisions([clean, rebucket, lone])
        }
        self.assertEqual(
            out[("Budget and Audit Committee", "2024-05-30")], ck.COLLISION
        )
        self.assertEqual(out[("City Council", "2024-05-30")], ck.COLLISION)
        self.assertEqual(out[("City Council", "2025-08-11")], ck.CLEAN)


class TestRekeyFilename(unittest.TestCase):
    def test_clean_inserts_time_only(self):
        self.assertEqual(
            ck.rekey_filename(
                "City Council Meeting 2025-08-11 - City of Clayton.txt",
                "2025-08-11",
                "2025-08-11 07_00 PM",
                "City Council",
                "City Council",
            ),
            "City Council Meeting 2025-08-11 07_00 PM - City of Clayton.txt",
        )

    def test_clean_compressed_part_suffix_preserved(self):
        self.assertEqual(
            ck.rekey_filename(
                "Clayton CA City Council Meeting 2025-08-11 - 000.mp4",
                "2025-08-11",
                "2025-08-11 07_00 PM",
                "City Council",
                "City Council",
            ),
            "Clayton CA City Council Meeting 2025-08-11 07_00 PM - 000.mp4",
        )

    def test_rebucket_swaps_stub_and_adds_time(self):
        self.assertEqual(
            ck.rekey_filename(
                "Clayton CA City Council Meeting 2024-05-30.mp4",
                "2024-05-30",
                "2024-05-30 04_00 PM",
                "City Council",
                "Budget and Audit Committee",
            ),
            "Clayton CA Budget and Audit Committee Meeting 2024-05-30 04_00 PM.mp4",
        )


class TestRemapRedisKey(unittest.TestCase):
    def test_clean_transcript_link(self):
        self.assertEqual(
            ck.remap_redis_key(
                "transcript_link.cc_mtg.2025-08-11",
                "2025-08-11",
                "2025-08-11 07_00 PM",
                "cc_mtg",
                "cc_mtg",
            ),
            "transcript_link.cc_mtg.2025-08-11 07_00 PM",
        )

    def test_rebucket_video_link_preserves_part(self):
        self.assertEqual(
            ck.remap_redis_key(
                "video_link.cc_mtg.2024-05-30.0",
                "2024-05-30",
                "2024-05-30 04_00 PM",
                "cc_mtg",
                "bac_mtg",
            ),
            "video_link.bac_mtg.2024-05-30 04_00 PM.0",
        )


class TestYouTube(unittest.TestCase):
    def test_title_date_hyphen_and_space_forms(self):
        self.assertEqual(
            ck.youtube_title_date("Clayton CA City Council Meeting 2021-09-21"),
            "2021-09-21",
        )
        self.assertEqual(
            ck.youtube_title_date("Clayton CA City Council Meeting 2025 05 19"),
            "2025-05-19",
        )
        self.assertIsNone(ck.youtube_title_date("Quick and dirty app demo"))

    def test_canonical_title_colon_datetime(self):
        self.assertEqual(
            ck.youtube_canonical_title(
                "Clayton CA City Council Meeting 2021-09-21",
                CITY_COUNCIL,
                "2021-09-21 07_00 PM",
            ),
            "Clayton CA City Council Meeting 2021-09-21 07:00 PM",
        )

    def test_meeting_type_matches_display_name_including_ghad(self):
        # Titles embed display_name; GHAD's title_match differs, so mt_classify
        # would miss it — the display-name matcher must not.
        self.assertIs(
            ck._youtube_meeting_type("Clayton CA GHAD Meeting 2024-05-01"), GHAD
        )
        self.assertIs(
            ck._youtube_meeting_type("Clayton CA City Council Meeting 2021-09-21"),
            CITY_COUNCIL,
        )
        self.assertIsNone(ck._youtube_meeting_type("Quick and dirty app demo"))

    def test_canonical_title_preserves_part_and_rebucket_name(self):
        self.assertEqual(
            ck.youtube_canonical_title(
                "Clayton CA City Council Meeting 2024-05-30 part 2",
                BUDGET_AND_AUDIT,
                "2024-05-30 04_00 PM",
            ),
            "Clayton CA Budget and Audit Committee Meeting 2024-05-30 04:00 PM part 2",
        )


class TestDriveChildName(unittest.TestCase):
    def test_swaps_folder_prefix_keeps_asset_tag(self):
        self.assertEqual(
            ck.drive_child_name(
                "City Council Meeting 2024-05-30 - Transcript",
                "City Council Meeting 2024-05-30",
                "Budget and Audit Committee Meeting 2024-05-30 04:00 PM",
            ),
            "Budget and Audit Committee Meeting 2024-05-30 04:00 PM - Transcript",
        )


class TestInventoryDisk(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.root = td.name

    def _touch(self, *parts):
        path = os.path.join(self.root, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()

    def test_collects_only_date_only_real_files(self):
        self._touch(
            "Compressed",
            "City Council",
            "Clayton CA City Council Meeting 2025-08-11 - 000.mp4",
        )
        self._touch(
            "Audio",
            "City Council",
            "City Council Meeting 2025-08-11 - City of Clayton.m4a",
        )
        # datetime-keyed: ignored
        self._touch(
            "Compressed",
            "City Council",
            "Clayton CA City Council Meeting 2026-01-06 06_55 PM - 000.mp4",
        )
        # macOS sidecar: ignored
        self._touch(
            "Audio",
            "City Council",
            "._City Council Meeting 2025-08-11 - City of Clayton.m4a",
        )
        # unknown bucket: ignored
        self._touch("Audio", ".tmp", "City Council Meeting 2025-08-11.m4a")

        found = ck.inventory_disk(self.root)
        self.assertEqual(set(found), {("City Council", "2025-08-11")})
        self.assertEqual(
            sorted(found[("City Council", "2025-08-11")]),
            [
                ("Audio", "City Council Meeting 2025-08-11 - City of Clayton.m4a"),
                ("Compressed", "Clayton CA City Council Meeting 2025-08-11 - 000.mp4"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
