import unittest

from src.processors.helpers.enrich_links import (
    assemble_enriched_markdown,
    format_hms,
    youtube_at,
)
from src.processors.helpers.section_summary import NOT_TAKEN_UP

VIDEO = "https://www.youtube.com/watch?v=OomXeI9fpIU"


def _section(index, title, summary, t_start, keywords=None):
    return {
        "item_index": index,
        "agenda_item": title,
        "summary": summary,
        "t_start": t_start,
        "keywords": keywords or [],
    }


class TestFormatHms(unittest.TestCase):
    def test_sub_hour_has_no_hour_field(self):
        self.assertEqual(format_hms(19), "0:19")
        self.assertEqual(format_hms(64), "1:04")
        self.assertEqual(format_hms(2468), "41:08")

    def test_hour_and_over_shows_hours(self):
        self.assertEqual(format_hms(3600), "1:00:00")
        self.assertEqual(format_hms(3723), "1:02:03")


class TestYoutubeAt(unittest.TestCase):
    def test_appends_offset_with_ampersand_when_query_present(self):
        self.assertEqual(youtube_at(VIDEO, 64), f"{VIDEO}&t=64s")

    def test_uses_question_mark_when_no_query(self):
        self.assertEqual(
            youtube_at("https://youtu.be/OomXeI9fpIU", 64),
            "https://youtu.be/OomXeI9fpIU?t=64s",
        )


class TestAssembleEnrichedMarkdown(unittest.TestCase):
    def test_adds_a_watch_link_per_reached_section(self):
        md = assemble_enriched_markdown(
            [_section(3, "Public Comment", "Residents spoke.", 64, ["comment"])],
            VIDEO,
        )
        self.assertIn("## Item 3: Public Comment", md)
        self.assertIn(f"[▶ Watch from 1:04]({VIDEO}&t=64s)", md)
        self.assertIn("Residents spoke.", md)
        self.assertIn("**Keywords:** comment", md)

    def test_no_link_for_a_not_taken_up_item(self):
        md = assemble_enriched_markdown(
            [_section(7, "Public Hearings", NOT_TAKEN_UP, 2681)], VIDEO
        )
        self.assertNotIn("Watch from", md)
        self.assertIn(NOT_TAKEN_UP, md)

    def test_no_link_when_section_has_no_timestamp(self):
        md = assemble_enriched_markdown(
            [_section(1, "Thing", "Happened.", None)], VIDEO
        )
        self.assertNotIn("Watch from", md)

    def test_without_video_is_identical_to_the_unlinked_assembly(self):
        # The enrichment is purely additive: with no video, the output must match the
        # summary stage's base .md exactly (heading + prose + keywords, link omitted),
        # so an un-enrichable meeting loses nothing.
        sections = [
            _section(1, "Call to Order", "Roll was called.", 19),
            _section(3, "Public Comment", "Residents spoke.", 64, ["comment"]),
        ]
        got = assemble_enriched_markdown(sections, None)
        expected = (
            "## Item 1: Call to Order\n\nRoll was called.\n\n"
            "## Item 3: Public Comment\n\nResidents spoke.\n\n"
            "**Keywords:** comment\n"
        )
        self.assertEqual(got, expected)


if __name__ == "__main__":
    unittest.main()
