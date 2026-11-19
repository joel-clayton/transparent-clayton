import unittest
from unittest.mock import patch

from src.processors import enrich
from src.processors.enrich import Enricher
from src.types import SourceType

NEW = "2026-10-06 07_00 PM"
DONE = "2026-09-01 07_00 PM"


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


class TestEnricherPassThrough(unittest.TestCase):
    def test_process_for_date_flags_the_wiki(self):
        e = Enricher(SourceType.CITY_COUNCIL_MEETING)
        with patch.object(enrich, "r") as r_mock:
            e.process_for_date(NEW)
        r_mock.sadd.assert_called_once()
        # the WIKI_REFRESH set for this type, keyed by meeting date
        self.assertIn("wiki_refresh", r_mock.sadd.call_args.args[0])
        self.assertEqual(r_mock.sadd.call_args.args[1], NEW)

    def test_process_enriches_only_the_unenriched_and_marks_done(self):
        """A summarized-but-not-yet-enriched meeting is flagged for the wiki and
        recorded done; an already-enriched one is left alone (no re-flag)."""
        e = Enricher(SourceType.CITY_COUNCIL_MEETING)
        with (
            patch.object(enrich, "scan_summaries", return_value=({NEW}, {DONE})),
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
