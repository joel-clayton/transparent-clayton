import unittest
from unittest.mock import MagicMock, patch

from pywikibot.textlib import Section

from src.meeting_types import CITY_COUNCIL
from src.processors.constants import WIKI_MTG_CANCELLED
from src.processors.update_wiki import (
    WikiUpdater,
    _humanize_meeting_key,
    insert_sections_in_date_order,
    render_meeting_table_row,
    render_no_materials_page,
)


class TestInsertSectionsInDateOrder(unittest.TestCase):
    def _sec(self, title):
        return Section(title=f"== {title} ==", content="\nbody\n")

    def _order(self, current_titles, new_title, new_key):
        current = [self._sec(t) for t in current_titles]
        result = insert_sections_in_date_order(current, [self._sec(new_title)], new_key)
        return [s.title.strip("= ").strip() for s in result]

    def test_older_date_lands_in_chronological_slot(self):
        # The regression: re-adding an older date must not jump to the top.
        order = self._order(
            ["September 15, 2026", "July 28, 2026", "June 02, 2026"],
            "August 18, 2026",
            "2026-08-18",
        )
        self.assertEqual(
            order,
            ["September 15, 2026", "August 18, 2026", "July 28, 2026", "June 02, 2026"],
        )

    def test_newest_date_goes_to_top(self):
        order = self._order(
            ["July 28, 2026", "June 02, 2026"], "September 15, 2026", "2026-09-15"
        )
        self.assertEqual(order[0], "September 15, 2026")

    def test_oldest_date_goes_to_bottom(self):
        order = self._order(
            ["September 15, 2026", "July 28, 2026"], "January 6, 2026", "2026-01-06"
        )
        self.assertEqual(order[-1], "January 6, 2026")


class TestUpdatePageSectionsCreatesPage(unittest.TestCase):
    def test_saves_even_when_page_does_not_exist(self):
        updater = WikiUpdater.__new__(WikiUpdater)
        updater.site = MagicMock()
        page = MagicMock()
        page.exists.return_value = False  # a brand-new year page
        with patch("src.processors.update_wiki.pywikibot.Page", return_value=page):
            updater.update_page_sections_for_page(
                "List of 2026 GHAD Meetings",
                [Section(title="== July 07, 2026 ==", content="\nbody\n")],
                "2026-07-07 06_30 PM",
            )
        page.save.assert_called_once()  # created, not silently skipped

    def test_no_save_when_there_are_no_sections(self):
        updater = WikiUpdater.__new__(WikiUpdater)
        updater.site = MagicMock()
        page = MagicMock()
        with patch("src.processors.update_wiki.pywikibot.Page", return_value=page):
            updater.update_page_sections_for_page("Any Page", [], "2026-07-07")
        page.save.assert_not_called()


class TestFormatCancelledSection(unittest.TestCase):
    def _updater(self):
        # Bypass __init__ (which authenticates to the wiki); the cancelled branch
        # needs only input_keys, and we stub the durable-link lookup so no Redis.
        updater = WikiUpdater.__new__(WikiUpdater)
        updater.meeting_type = CITY_COUNCIL
        updater.input_keys = []
        updater.get_doc_links_for_key = lambda key: {}
        return updater

    def test_cancelled_meeting_links_notice_and_omits_table(self):
        sections = self._updater().format_wiki_section(
            {
                "key": "2026-08-11 07_00 PM",
                "cancelled": True,
                "minutes_and_supplemental_materials": {
                    "Notice of Cancelation": "https://example.com/notice.pdf"
                },
            }
        )
        self.assertEqual(len(sections), 1)  # no AI-summary section
        content = sections[0].content
        self.assertIn("Cancelled", content)
        self.assertIn("[https://example.com/notice.pdf Notice of Cancelation]", content)
        self.assertNotIn("wikitable", content)

    def test_cancelled_meeting_without_a_notice_shows_bare_marker(self):
        sections = self._updater().format_wiki_section(
            {"key": "2026-08-11 07_00 PM", "cancelled": True}
        )
        self.assertEqual(sections[0].content, WIKI_MTG_CANCELLED)


class TestFormatNoAudioSection(unittest.TestCase):
    def test_no_audio_meeting_keeps_video_row_adds_note_no_ai(self):
        updater = WikiUpdater.__new__(WikiUpdater)
        updater.meeting_type = CITY_COUNCIL
        updater.input_keys = []
        updater._no_audio_cache = {"2026-08-26 06_00 PM"}
        updater.get_video_backup_links_for_key = lambda key: ["https://yt/x"]
        updater.get_doc_links_for_key = lambda key: {}
        sections = updater.format_wiki_section(
            {"key": "2026-08-26 06_00 PM", "video": "https://cpmedia/x.mp4"}
        )
        self.assertEqual(len(sections), 1)  # no AI-summary section (no transcript)
        content = sections[0].content
        self.assertIn("wikitable", content)  # video row is kept
        self.assertIn("no usable audio", content)  # explanatory note appended


class TestGeneralMeetingNameHeading(unittest.TestCase):
    def _updater(self, meeting_type):
        u = WikiUpdater.__new__(WikiUpdater)
        u.meeting_type = meeting_type
        u.input_keys = []
        u._no_audio_cache = set()
        u.get_video_backup_links_for_key = lambda key: []
        u.get_doc_links_for_key = lambda key: {}
        return u

    def test_general_shows_event_name_above_table(self):
        from src.meeting_types import GENERAL

        sections = self._updater(GENERAL).format_wiki_section(
            {
                "key": "2026-08-26 06_00 PM",
                "name": "Town Hall",
                "video": "https://x/v.mp4",
            }
        )
        content = sections[0].content
        self.assertIn("'''Town Hall'''", content)
        self.assertLess(content.index("Town Hall"), content.index("wikitable"))

    def test_other_types_do_not_show_the_name(self):
        from src.meeting_types import CITY_COUNCIL

        sections = self._updater(CITY_COUNCIL).format_wiki_section(
            {
                "key": "2026-08-18 07_00 PM",
                "name": "Regular Meeting",
                "video": "https://x/v.mp4",
            }
        )
        self.assertNotIn("Regular Meeting", sections[0].content)


class TestRenderMeetingTableRow(unittest.TestCase):
    def test_full_meeting_includes_present_cells_and_backups(self):
        row = render_meeting_table_row(
            {
                "agenda": "https://x/agenda.pdf",
                "video": "https://x/v.mp4",
                "agenda_packet": "https://x/packet.pdf",
                "minutes_and_supplemental_materials": {
                    "Staff Report": "https://x/s.pdf"
                },
                "transcript_link": "https://x/t.txt",
            },
            ["https://yt/1", "https://yt/2"],
        )
        self.assertIn("[https://x/v.mp4 Video]", row)
        self.assertIn("[https://x/s.pdf Staff Report]", row)
        self.assertIn("[https://x/t.txt Transcript]", row)
        self.assertIn("Video Backup part 2", row)

    def test_docs_only_omits_video_and_transcript(self):
        row = render_meeting_table_row(
            {"agenda": "https://x/a.pdf", "agenda_packet": "https://x/p.pdf"}, []
        )
        self.assertIn("Agenda]", row)
        self.assertIn("Agenda Packet]", row)
        self.assertNotIn("Video", row)
        self.assertNotIn("Transcript", row)
        self.assertNotIn("None", row)

    def test_prefers_durable_archived_links(self):
        row = render_meeting_table_row(
            {
                "agenda": "https://src/a.pdf",
                "agenda_packet": "https://src/a.pdf",
                "minutes_and_supplemental_materials": {
                    "Staff Report": "https://src/s.pdf"
                },
            },
            [],
            doc_links={
                "Agenda Packet": "https://drive/a",
                "Staff Report": "https://drive/s",
            },
        )
        # Durable links replace the expiring source URLs everywhere (incl. the
        # Agenda cell, since agenda == agenda_packet in CivicClerk).
        self.assertIn("[https://drive/a Agenda]", row)
        self.assertIn("[https://drive/a Agenda Packet]", row)
        self.assertIn("[https://drive/s Staff Report]", row)
        self.assertNotIn("src/", row)


class TestRenderNoMaterialsPage(unittest.TestCase):
    def test_lists_entries_newest_first_with_type_label(self):
        body = render_no_materials_page(
            [
                ("City Council", "2026-06-03 07_00 PM"),
                ("Planning Commission", "2026-07-01 07_00 PM"),
            ]
        )
        self.assertLess(body.index("July 01, 2026"), body.index("June 03, 2026"))
        self.assertIn("July 01, 2026 07:00 PM — Planning Commission", body)
        self.assertIn("June 03, 2026 07:00 PM — City Council", body)

    def test_empty_uses_placeholder(self):
        self.assertEqual(
            render_no_materials_page([]),
            "No meetings without published materials have been recorded.",
        )

    def test_renders_no_audio_section_alongside_no_materials(self):
        body = render_no_materials_page(
            [("City Council", "2026-06-03 07_00 PM")],
            no_audio_entries=[("General", "2026-08-26 06_00 PM")],
        )
        self.assertIn("Meetings Without Usable Audio", body)
        self.assertIn("August 26, 2026 06:00 PM — General", body)
        self.assertIn("June 03, 2026 07:00 PM — City Council", body)

    def test_no_audio_only_still_renders_that_section(self):
        body = render_no_materials_page(
            [], no_audio_entries=[("GHAD", "2026-07-07 06_30 PM")]
        )
        self.assertIn("Meetings Without Usable Audio", body)
        self.assertIn("July 07, 2026 06:30 PM — GHAD", body)


class TestHumanizeMeetingKey(unittest.TestCase):
    def test_formats_known_shapes_else_passthrough(self):
        cases = [
            ("2026-06-03 07_00 PM", "June 03, 2026 07:00 PM"),
            ("2026-06-03", "June 03, 2026"),
            ("not a date", "not a date"),
        ]
        for key, expected in cases:
            with self.subTest(key):
                self.assertEqual(_humanize_meeting_key(key), expected)


if __name__ == "__main__":
    unittest.main()
