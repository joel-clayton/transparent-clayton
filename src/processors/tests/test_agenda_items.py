import unittest

from src.processors.helpers.agenda_items import (
    is_procedural,
    parse_agenda_items,
)

AGENDA = """CLAYTON CITY COUNCIL
REGULAR MEETING AGENDA
Jeff Wan, Mayor
1. CALL TO ORDER AND ROLL CALL
2. PLEDGE OF ALLEGIANCE
3. PUBLIC COMMENT ON NON-AGENDA ITEMS
Each speaker is limited to three minutes.
4. CONSENT CALENDAR
(a) Approve September 15, 2026 Minutes
(b) Adoption of Resolution No. 39-2026
5. RECOGNITIONS AND PRESENTATIONS
9. ADJOURNMENT
"""


class TestParseAgendaItems(unittest.TestCase):
    def test_parses_numbered_uppercase_items_in_order(self):
        items = parse_agenda_items(AGENDA)
        self.assertEqual(
            [(it.number, it.title) for it in items],
            [
                (1, "Call to Order and Roll Call"),
                (2, "Pledge of Allegiance"),
                (3, "Public Comment on Non-agenda Items"),
                (4, "Consent Calendar"),
                (5, "Recognitions and Presentations"),
                (9, "Adjournment"),
            ],
        )

    def test_body_captures_subitems_until_next_heading(self):
        items = {it.number: it for it in parse_agenda_items(AGENDA)}
        self.assertIn("Resolution No. 39-2026", items[4].body)
        self.assertIn("Approve September 15, 2026 Minutes", items[4].body)
        # body stops before the next heading
        self.assertNotIn("RECOGNITIONS", items[4].body)

    def test_procedural_flags(self):
        items = {it.number: it for it in parse_agenda_items(AGENDA)}
        self.assertTrue(items[1].procedural)  # roll call
        self.assertTrue(items[2].procedural)  # pledge
        self.assertTrue(items[9].procedural)  # adjournment
        self.assertFalse(items[3].procedural)  # public comment
        self.assertFalse(items[4].procedural)  # consent calendar
        self.assertFalse(items[5].procedural)  # presentations

    def test_deduplicates_reprinted_heading(self):
        text = "1. CALL TO ORDER AND ROLL CALL\n1. CALL TO ORDER AND ROLL CALL\n"
        items = parse_agenda_items(text)
        self.assertEqual(len(items), 1)

    def test_empty_agenda_yields_no_items(self):
        self.assertEqual(parse_agenda_items(""), [])
        self.assertEqual(parse_agenda_items("no numbered headings here"), [])

    def test_lowercase_title_line_is_not_an_item(self):
        # Prose lines that happen to start "N." (mixed case) are not top-level items.
        self.assertEqual(parse_agenda_items("1. this is a sentence, not a header"), [])


class TestIsProcedural(unittest.TestCase):
    def test_keyword_matches(self):
        self.assertTrue(is_procedural("Call to Order"))
        self.assertTrue(is_procedural("Closed Session"))
        self.assertTrue(is_procedural("Approval of Minutes"))
        self.assertFalse(is_procedural("Public Hearings"))
        self.assertFalse(is_procedural("Action Items"))


if __name__ == "__main__":
    unittest.main()
