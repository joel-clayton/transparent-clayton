import json
import unittest
from unittest.mock import patch

from src.processors.helpers.agenda_items import AgendaItem
from src.processors.helpers import section_summary
from src.processors.helpers.section_summary import (
    build_speaker_key,
    extract_keywords,
    procedural_line,
    summarize_section,
)


class TestProceduralLine(unittest.TestCase):
    def test_includes_title_and_timestamp(self):
        item = AgendaItem(1, "Call to Order and Roll Call", "X", True)
        self.assertEqual(
            procedural_line(item, 19), "Call to Order and Roll Call at [0:00:19]."
        )


class TestSummarizeSection(unittest.TestCase):
    def test_passes_agenda_body_and_span_and_disables_thinking(self):
        item = AgendaItem(4, "Consent Calendar", "CONSENT", False, body="Resolution 39")
        with patch.object(
            section_summary, "generate", return_value="  a summary  "
        ) as gen:
            out = summarize_section(item, "SPAN TEXT", model="m")
        self.assertEqual(out, "a summary")  # stripped
        kwargs = gen.call_args.kwargs
        self.assertIs(kwargs["think"], False)
        prompt = gen.call_args.args[1]
        self.assertIn("Resolution 39", prompt)
        self.assertIn("SPAN TEXT", prompt)


class TestBuildSpeakerKey(unittest.TestCase):
    def test_formats_label_name_lines_and_skips_incomplete(self):
        payload = json.dumps(
            {
                "speakers": [
                    {"label": "Speaker A", "name": "Jeff Wan, Mayor"},
                    {"label": "Speaker C", "name": "Jim Diaz, Councilmember"},
                    {"label": "Speaker Z"},  # no name -> skipped
                ]
            }
        )
        with patch.object(section_summary, "generate", return_value=payload):
            key = build_speaker_key("ROSTER", "ROLL CALL TEXT", model="m")
        self.assertEqual(
            key, "Speaker A = Jeff Wan, Mayor\nSpeaker C = Jim Diaz, Councilmember"
        )

    def test_empty_roll_call_skips_model(self):
        with patch.object(section_summary, "generate") as gen:
            self.assertEqual(build_speaker_key("ROSTER", "   ", model="m"), "")
        gen.assert_not_called()

    def test_unparseable_returns_empty(self):
        with patch.object(section_summary, "generate", return_value="nope"):
            self.assertEqual(build_speaker_key("R", "roll", model="m"), "")


class TestExtractKeywords(unittest.TestCase):
    def test_parses_dedupes_and_caps(self):
        payload = json.dumps(
            {"keywords": ["Measure A", "measure a", "open space", "", "trails"]}
        )
        with patch.object(section_summary, "generate", return_value=payload):
            kws = extract_keywords("summary text", model="m", max_keywords=2)
        self.assertEqual(kws, ["Measure A", "open space"])  # dedupe + cap at 2

    def test_empty_text_skips_model(self):
        with patch.object(section_summary, "generate") as gen:
            self.assertEqual(extract_keywords("   ", model="m"), [])
        gen.assert_not_called()

    def test_unparseable_json_returns_empty(self):
        with patch.object(section_summary, "generate", return_value="oops"):
            self.assertEqual(extract_keywords("text", model="m"), [])


if __name__ == "__main__":
    unittest.main()
