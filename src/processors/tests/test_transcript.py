import unittest

from src.processors.helpers.transcript import (
    hms_to_seconds,
    parse_utterances,
    render_span,
    seconds_to_hms,
    utterances_in_span,
)

TRANSCRIPT = """Some header line to ignore
[Speaker A] (0:00:19 - 0:00:26)
All right, it is 7 PM.
I will call this meeting to order.

[Speaker B] (0:00:27 - 0:00:28)
Council Member Diaz.

[Speaker A] (1:02:03 - 1:02:10)
We are adjourned.
"""


class TestTimeHelpers(unittest.TestCase):
    def test_round_trip(self):
        self.assertEqual(hms_to_seconds("1:02:03"), 3723)
        self.assertEqual(seconds_to_hms(3723), "1:02:03")
        self.assertEqual(seconds_to_hms(19), "0:00:19")

    def test_negative_clamped(self):
        self.assertEqual(seconds_to_hms(-5), "0:00:00")


class TestParseUtterances(unittest.TestCase):
    def test_parses_blocks_and_ignores_preamble(self):
        us = parse_utterances(TRANSCRIPT)
        self.assertEqual(len(us), 3)
        self.assertEqual(us[0].speaker, "Speaker A")
        self.assertEqual(us[0].start, 19)
        self.assertEqual(us[0].end, 26)
        # multi-line body is joined
        self.assertIn("call this meeting to order", us[0].text)
        self.assertEqual(us[2].start, 3723)

    def test_empty_transcript(self):
        self.assertEqual(parse_utterances(""), [])
        self.assertEqual(parse_utterances("no headers at all\njust text"), [])


class TestSpanHelpers(unittest.TestCase):
    def test_utterances_in_span_half_open(self):
        us = parse_utterances(TRANSCRIPT)
        span = utterances_in_span(us, 19, 27)  # [19, 27) -> only the first
        self.assertEqual([u.start for u in span], [19])
        to_end = utterances_in_span(us, 27, None)
        self.assertEqual([u.start for u in to_end], [27, 3723])

    def test_render_span_uses_start_timestamp(self):
        us = parse_utterances(TRANSCRIPT)
        rendered = render_span(us[:1])
        self.assertIn("[Speaker A] (0:00:19)", rendered)
        self.assertIn("call this meeting to order", rendered)


if __name__ == "__main__":
    unittest.main()
