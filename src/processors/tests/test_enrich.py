import json
import unittest
from unittest.mock import mock_open, patch

from src.processors import enrich
from src.processors.enrich import Enricher
from src.types import SourceType

NEW = "2026-10-06 07_00 PM"
DONE = "2026-09-01 07_00 PM"
VIDEO = "https://www.youtube.com/watch?v=OomXeI9fpIU"


class TestEnricherWorklist(unittest.TestCase):
    def test_input_is_union_of_indexed_and_legacy_summaries(self):
        e = Enricher(SourceType.CITY_COUNCIL_MEETING)
        with patch.object(enrich, "scan_summaries", return_value=({NEW}, {DONE})):
            self.assertEqual(e.gather_input_dates(), [DONE, NEW])

    def test_output_is_the_enriched_ledger(self):
        e = Enricher(SourceType.CITY_COUNCIL_MEETING)
        with patch.object(enrich, "r") as r_mock:
            r_mock.hkeys.return_value = [NEW.encode("utf-8")]
            self.assertEqual(e.gather_output_dates(), [NEW])
            r_mock.hkeys.assert_called_once_with(e.redis_key)


class TestSinglePartVideoUrl(unittest.TestCase):
    def test_returns_the_url_for_exactly_one_part(self):
        e = Enricher(SourceType.CITY_COUNCIL_MEETING)
        with patch.object(enrich, "r") as r_mock:
            r_mock.scan_iter.return_value = [f"video_link.cc_mtg.{NEW}.1"]
            r_mock.get.return_value = VIDEO.encode("utf-8")
            self.assertEqual(e._single_part_video_url(NEW), VIDEO)

    def test_none_for_no_video(self):
        e = Enricher(SourceType.CITY_COUNCIL_MEETING)
        with patch.object(enrich, "r") as r_mock:
            r_mock.scan_iter.return_value = []
            self.assertIsNone(e._single_part_video_url(NEW))

    def test_none_for_multi_part(self):
        # Multi-part per-part offset mapping is a later increment, so skip the link.
        e = Enricher(SourceType.CITY_COUNCIL_MEETING)
        with patch.object(enrich, "r") as r_mock:
            r_mock.scan_iter.return_value = [
                f"video_link.cc_mtg.{NEW}.1",
                f"video_link.cc_mtg.{NEW}.2",
            ]
            self.assertIsNone(e._single_part_video_url(NEW))


class TestProcessForDate(unittest.TestCase):
    def test_pass_through_when_no_sections_json_only_flags_wiki(self):
        e = Enricher(SourceType.CITY_COUNCIL_MEETING)
        with (
            patch.object(enrich.os.path, "exists", return_value=False),
            patch.object(e, "_atomic_write") as write,
            patch.object(enrich, "r") as r_mock,
        ):
            e.process_for_date(NEW)
        write.assert_not_called()  # legacy summary: base .md untouched
        r_mock.sadd.assert_called_once()
        self.assertIn("wiki_refresh", r_mock.sadd.call_args.args[0])
        self.assertEqual(r_mock.sadd.call_args.args[1], NEW)

    def test_writes_enriched_md_and_flags_wiki_when_sections_and_video(self):
        e = Enricher(SourceType.CITY_COUNCIL_MEETING)
        payload = json.dumps(
            {
                "sections": [
                    {
                        "item_index": 3,
                        "agenda_item": "Public Comment",
                        "summary": "Residents spoke.",
                        "t_start": 64,
                        "keywords": [],
                    }
                ]
            }
        )
        with (
            patch.object(enrich.os.path, "exists", return_value=True),
            patch("builtins.open", mock_open(read_data=payload)),
            patch.object(e, "_single_part_video_url", return_value=VIDEO),
            patch.object(e, "_atomic_write") as write,
            patch.object(enrich, "r") as r_mock,
        ):
            e.process_for_date(NEW)
        # the enriched .md (with the deep link) is written out...
        write.assert_called_once()
        written_md = write.call_args.args[1]
        self.assertIn(f"[▶ Watch from 1:04]({VIDEO}&t=64s)", written_md)
        # ...and the wiki is flagged.
        r_mock.sadd.assert_called_once()
        self.assertEqual(r_mock.sadd.call_args.args[1], NEW)

    def test_pass_through_when_sections_but_no_video(self):
        e = Enricher(SourceType.CITY_COUNCIL_MEETING)
        payload = json.dumps(
            {
                "sections": [
                    {"item_index": 1, "agenda_item": "x", "summary": "y", "t_start": 1}
                ]
            }
        )
        with (
            patch.object(enrich.os.path, "exists", return_value=True),
            patch("builtins.open", mock_open(read_data=payload)),
            patch.object(e, "_single_part_video_url", return_value=None),
            patch.object(e, "_atomic_write") as write,
            patch.object(enrich, "r") as r_mock,
        ):
            e.process_for_date(NEW)
        write.assert_not_called()  # nothing to add: leave the base .md as-is
        r_mock.sadd.assert_called_once()


class TestProcessLedger(unittest.TestCase):
    def test_process_enriches_only_the_unenriched_and_marks_done(self):
        """A summarized-but-not-yet-enriched meeting is flagged for the wiki and
        recorded done; an already-enriched one is left alone (no re-flag)."""
        e = Enricher(SourceType.CITY_COUNCIL_MEETING)
        with (
            patch.object(enrich, "scan_summaries", return_value=({NEW}, {DONE})),
            patch.object(enrich.os.path, "exists", return_value=False),  # pass-through
            patch.object(enrich, "r") as enrich_r,
            patch("src.processors.process.r") as proc_r,
        ):
            enrich_r.hkeys.return_value = [DONE.encode("utf-8")]  # already enriched
            e.process()
        # only NEW gets a wiki flag...
        enrich_r.sadd.assert_called_once()
        self.assertEqual(enrich_r.sadd.call_args.args[1], NEW)
        # ...and only NEW is marked done in the ledger (base Processor.process)
        proc_r.hset.assert_called_once()
        self.assertEqual(proc_r.hset.call_args.args[0], e.redis_key)
        self.assertEqual(proc_r.hset.call_args.args[1], NEW)


if __name__ == "__main__":
    unittest.main()
