import unittest

from src.processors.update_wiki import (
    _humanize_meeting_key,
    render_meeting_table_row,
    render_no_materials_page,
)


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
