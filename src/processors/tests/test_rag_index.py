import json
import os
import tempfile
import unittest
from unittest.mock import patch

from src.processors import rag_index
from src.processors.rag_index import RagIndexer
from src.processors.helpers.rag_store import connect as real_connect
from src.types import JobType, SourceType


class TestHealMissingMarkdown(unittest.TestCase):
    def test_rebuilds_md_from_sections_json(self):
        idx = RagIndexer(SourceType.CITY_COUNCIL_MEETING)
        tmp = tempfile.mkdtemp()
        md = os.path.join(tmp, "m.md")
        sj = os.path.join(tmp, "m.sections.json")
        with open(sj, "w") as fh:
            json.dump(
                {
                    "sections": [
                        {
                            "item_index": 5,
                            "agenda_item": "Recognitions",
                            "summary": "A presentation happened.",
                            "keywords": ["kw1"],
                        }
                    ]
                },
                fh,
            )
        idx._scan_summaries = lambda: ({"2026-10-06 07_00 PM"}, set())  # type: ignore
        idx.construct_filepath_for_date = lambda date, job_type=None: md  # type: ignore
        idx._sections_json_path = lambda date: sj  # type: ignore

        with patch.object(rag_index, "r") as r_mock:
            idx._heal_missing_markdown()

        content = open(md, encoding="utf-8").read()
        self.assertIn("## Item 5: Recognitions", content)
        self.assertIn("A presentation happened.", content)
        self.assertIn("**Keywords:** kw1", content)
        # a rebuilt .md flags the meeting so the wiki re-renders it (TRA-178)
        r_mock.sadd.assert_called_once()
        self.assertIn("wiki_refresh", r_mock.sadd.call_args.args[0])
        self.assertEqual(r_mock.sadd.call_args.args[1], "2026-10-06 07_00 PM")

    def test_leaves_existing_md_alone(self):
        idx = RagIndexer(SourceType.CITY_COUNCIL_MEETING)
        tmp = tempfile.mkdtemp()
        md = os.path.join(tmp, "m.md")
        with open(md, "w") as fh:
            fh.write("ORIGINAL")
        idx._scan_summaries = lambda: ({"k"}, set())  # type: ignore
        idx.construct_filepath_for_date = lambda date, job_type=None: md  # type: ignore
        idx._sections_json_path = lambda date: os.path.join(tmp, "m.sections.json")  # type: ignore
        idx._heal_missing_markdown()
        self.assertEqual(open(md).read(), "ORIGINAL")


class TestNoAgendaFallback(unittest.TestCase):
    """With no parseable agenda the whole meeting is summarized as one reached span,
    not marked 'not taken up' (regression: the synthetic item run through the
    segmenter found no chair cue and produced NOT_TAKEN_UP for the whole meeting)."""

    def test_whole_meeting_summarized_when_no_agenda(self):
        idx = RagIndexer(SourceType.CITY_COUNCIL_MEETING)
        tmp = tempfile.mkdtemp()
        transcript = os.path.join(tmp, "t.txt")
        with open(transcript, "w") as fh:
            fh.write("[Speaker A] (0:00:01 - 0:00:05)\nWe begin the meeting.\n")
        md = os.path.join(tmp, "m.md")
        sj = os.path.join(tmp, "m.sections.json")
        db = os.path.join(tmp, "x.db")

        def cfd(date, job_type=None):
            return transcript if job_type == JobType.TRANSCRIBE_AUDIO else md

        idx.construct_filepath_for_date = cfd  # type: ignore
        idx._sections_json_path = lambda date: sj  # type: ignore

        with (
            patch.object(rag_index, "find_document", return_value=None),
            patch.object(rag_index, "segment_meeting") as seg,
            patch.object(
                rag_index, "summarize_section", return_value="WHOLE MEETING SUMMARY"
            ),
            patch.object(rag_index, "extract_keywords", return_value=[]),
            patch.object(rag_index, "build_speaker_key", return_value=""),
            patch.object(rag_index, "model_digest", return_value=None),
            patch.object(rag_index, "embed_document", return_value=[0.1, 0.2, 0.3]),
            patch.object(rag_index, "connect", lambda *a, **k: real_connect(db)),
            patch.object(rag_index, "r") as r_mock,
        ):
            idx.process_for_date("2026-10-06 07_00 PM")

        seg.assert_not_called()  # no agenda -> segmenter skipped
        content = open(md, encoding="utf-8").read()
        self.assertIn("WHOLE MEETING SUMMARY", content)
        self.assertNotIn("not taken up", content.lower())
        payload = json.load(open(sj, encoding="utf-8"))
        self.assertEqual(len(payload["sections"]), 1)
        self.assertEqual(payload["sections"][0]["agenda_item"], "Meeting")
        # generating a summary flags the meeting for wiki refresh (TRA-178)
        r_mock.sadd.assert_called_once()
        self.assertIn("wiki_refresh", r_mock.sadd.call_args.args[0])


class TestFallbackSegments(unittest.TestCase):
    def _utts(self, n, chars):
        from src.processors.helpers.transcript import Utterance

        return [Utterance("A", i * 10, i * 10 + 5, "x" * chars) for i in range(n)]

    def test_single_chunk_is_one_meeting_section(self):
        idx = RagIndexer(SourceType.CITY_COUNCIL_MEETING)
        segs = idx._fallback_segments(self._utts(2, 50))
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0].title, "Meeting")
        self.assertTrue(segs[0].reached)

    def test_long_transcript_splits_into_parts(self):
        idx = RagIndexer(SourceType.CITY_COUNCIL_MEETING)
        # 10 utterances x 5000 chars = 50000 > FALLBACK_WINDOW_TOKENS*4 (32000)
        segs = idx._fallback_segments(self._utts(10, 5000))
        self.assertGreater(len(segs), 1)
        self.assertEqual([s.title for s in segs[:2]], ["Part 1", "Part 2"])
        self.assertTrue(all(s.reached for s in segs))


class TestAllUnreachedFallback(unittest.TestCase):
    """A parseable agenda whose segmentation detects zero boundaries (all items
    unreached) falls back to whole-meeting parts rather than emitting a meeting
    where every item reads 'not taken up'."""

    def test_falls_back_when_no_item_reached(self):
        from src.processors.helpers.segmentation import Segment

        idx = RagIndexer(SourceType.CITY_COUNCIL_MEETING)
        tmp = tempfile.mkdtemp()
        transcript = os.path.join(tmp, "t.txt")
        with open(transcript, "w") as fh:
            fh.write("[Speaker A] (0:00:01 - 0:00:05)\nMeeting content here.\n")
        md = os.path.join(tmp, "m.md")
        sj = os.path.join(tmp, "m.sections.json")
        db = os.path.join(tmp, "x.db")
        idx.construct_filepath_for_date = (  # type: ignore
            lambda date, job_type=None: (
                transcript if job_type == JobType.TRANSCRIBE_AUDIO else md
            )
        )
        idx._sections_json_path = lambda date: sj  # type: ignore

        all_unreached = [
            Segment(1, "Roll Call", True, False, 0, 0, []),
            Segment(2, "Action Items", False, False, 0, 0, []),
        ]
        with (
            patch.object(rag_index, "find_document", return_value="/agenda.pdf"),
            patch.object(
                rag_index, "pdf_to_text", return_value="1. ROLL CALL\n2. ACTION ITEMS"
            ),
            patch.object(rag_index, "segment_meeting", return_value=all_unreached),
            patch.object(rag_index, "summarize_section", return_value="REAL CONTENT"),
            patch.object(rag_index, "extract_keywords", return_value=[]),
            patch.object(rag_index, "build_speaker_key", return_value=""),
            patch.object(rag_index, "model_digest", return_value=None),
            patch.object(rag_index, "embed_document", return_value=[0.1, 0.2, 0.3]),
            patch.object(rag_index, "connect", lambda *a, **k: real_connect(db)),
            patch.object(rag_index, "r"),
        ):
            idx.process_for_date("2026-10-06 07_00 PM")

        content = open(md, encoding="utf-8").read()
        self.assertIn("REAL CONTENT", content)
        self.assertNotIn("not taken up", content.lower())


if __name__ == "__main__":
    unittest.main()
