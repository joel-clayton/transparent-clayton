import os
import tempfile
import unittest

from scripts import canonicalize_keys as ck


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

    def test_multiple_candidate_types_is_ambiguous(self):
        p = ck.classify(
            "City Council",
            "2021-06-08",
            [
                ev("2021-06-08T16:00:00Z", "Budget and Audit Committee"),
                ev("2021-06-08T19:00:00Z", "Planning Commission"),
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
