import unittest

from src.processors.helpers.agenda_items import (
    agenda_from_packet,
    agenda_preamble,
    is_procedural,
    minutes_roll_call,
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


MIXED_CASE_AGENDA = """BUDGET AND AUDIT COMMITTEE
REGULAR MEETING AGENDA
Committee Members
Kim Trupiano, Vice Mayor Jeff Wan, Councilember
1. Call to Order and Roll Call
2. UBS Financial Services (Interim City Manager)
a. Discuss City\u2019s investments
3. Review and provide feedback on proposed Scope of Work prior to the
preparation of the Request for Proposal (RFP) for Right of Way Landscape
Maintenance (City Engineer)
(View)
4. Future topics (Financial Advisor)
a. Potential spend down of reserves:
5. ADJOURNMENT
Please visit www.claytonca.gov for information on the next meeting.
STAFF REPORT
1. Options considered by staff were reviewed.
2. The committee may adopt the proposed scope.
"""


class TestMixedCaseAgenda(unittest.TestCase):
    """Some agendas print every heading in Title/sentence case (only Adjournment in
    caps); the strict all-caps pattern then found just Adjournment."""

    def test_parses_sequential_mixed_case_items_and_stops_at_adjournment(self):
        items = parse_agenda_items(MIXED_CASE_AGENDA)
        self.assertEqual([i.number for i in items], [1, 2, 3, 4, 5])
        self.assertEqual(
            items[1].title, "UBS Financial Services (Interim City Manager)"
        )
        # A heading the PDF wrapped across lines is joined into one title.
        self.assertTrue(
            items[2].title.startswith("Review and provide feedback on proposed")
        )
        self.assertTrue(items[2].title.endswith("Maintenance (City Engineer)"))
        # The list ends at Adjournment: the staff report's own numbered lines that
        # follow it never become items.
        self.assertEqual(items[-1].number, 5)
        self.assertEqual(items[-1].title, "Adjournment")
        self.assertTrue(items[-1].procedural)
        self.assertTrue(items[0].procedural)
        self.assertEqual(items[1].body, "a. Discuss City\u2019s investments")

    def test_preamble_stops_before_the_first_mixed_case_item(self):
        self.assertTrue(agenda_preamble(MIXED_CASE_AGENDA).endswith("Councilember"))

    def test_all_caps_agenda_ignores_mixed_case_numbered_prose(self):
        text = AGENDA + "\n1. A numbered sentence in some staff report.\n"
        self.assertEqual(
            [i.number for i in parse_agenda_items(text)],
            [i.number for i in parse_agenda_items(AGENDA)],
        )


PACKET = """CITY COUNCIL AGENDA
August 21, 2018
1. CALL TO ORDER AND ROLL CALL \u2013 Mayor Haydon.
2. PLEDGE OF ALLEGIANCE \u2013 led by Mayor Haydon.
3. CONSENT CALENDAR
10. CLOSED SESSION
1. Real Properties: 1005 and 1007 Oak Street, Clayton, CA
11. ADJOURNMENT - the City Council meeting of September 4, 2018 was canceled.
STAFF REPORT
12. HEADING IN A REPORT
"""


class TestAgendaFromPacket(unittest.TestCase):
    """Older meetings are archived only as an agenda packet: the agenda leads it and
    staff reports follow, so the text is cut after the Adjournment item."""

    def test_cuts_after_adjournment_and_reads_items_with_trailing_prose(self):
        agenda = agenda_from_packet(PACKET)
        self.assertNotIn("STAFF REPORT", agenda)
        items = parse_agenda_items(agenda)
        # items 1-2 carry "\u2013 Mayor Haydon." and the adjournment line carries prose
        self.assertEqual([i.number for i in items], [1, 2, 3, 10, 11])
        self.assertEqual(items[0].title, "Call to Order and Roll Call")
        self.assertTrue(items[0].procedural)
        self.assertEqual(items[-1].title, "Adjournment")

    def test_without_an_adjournment_line_only_the_leading_text_is_used(self):
        text = "1. CALL TO ORDER\n" + "filler line\n" * 5000
        self.assertEqual(len(agenda_from_packet(text)), 15000)
        self.assertEqual(agenda_from_packet("short"), "short")


class TestMinutesRollCall(unittest.TestCase):
    MINUTES = (
        "MINUTES\nOF THE\nREGULAR MEETING\nBUDGET & AUDIT COMMITTEE\n"
        "1. CALL TO ORDER AND ROLL CALL \u2013 The meeting was called to order at 4:00\n"
        "p.m. by Mayor Trupiano. Committee members present:\n"
        "Vice Mayor Wan and Mayor Trupiano. Staff present: City Manager, Kris Lofthus\n"
        "2. PUBLIC COMMENT ON NON - AGENDA ITEMS\nNo one spoke.\n"
    )

    def test_extracts_attendance_paragraph_up_to_next_item(self):
        text = minutes_roll_call(self.MINUTES)
        self.assertIn("Vice Mayor Wan and Mayor Trupiano", text)
        self.assertIn("Staff present: City Manager, Kris Lofthus", text)
        self.assertNotIn("PUBLIC COMMENT", text)
        self.assertNotIn("\n", text)  # whitespace collapsed to one line

    def test_empty_when_no_roll_call_or_no_attendance(self):
        self.assertEqual(minutes_roll_call(""), "")
        self.assertEqual(minutes_roll_call("1. APPROVAL OF MINUTES\nDone.\n"), "")
        # A call-to-order line that names no attendees is not a roster.
        self.assertEqual(
            minutes_roll_call("1. CALL TO ORDER\nThe meeting began.\n2. NEXT\n"), ""
        )

    def test_bounded_length(self):
        long = "1. ROLL CALL\nMembers present: " + "x " * 2000
        self.assertLessEqual(len(minutes_roll_call(long, max_chars=100)), 100)


class TestIsProcedural(unittest.TestCase):
    def test_keyword_matches(self):
        self.assertTrue(is_procedural("Call to Order"))
        self.assertTrue(is_procedural("Closed Session"))
        self.assertTrue(is_procedural("Approval of Minutes"))
        self.assertFalse(is_procedural("Public Hearings"))
        self.assertFalse(is_procedural("Action Items"))


if __name__ == "__main__":
    unittest.main()
