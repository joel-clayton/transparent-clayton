import unittest
from datetime import date, datetime

from src.scrapers.cc_meetings import (
    get_clip_id_from_url,
    get_inner_text_from_html,
    parse_date_input,
    parse_multiple_urls_from_html,
    parse_url_from_html,
)


class TestParseDateInput(unittest.TestCase):
    def test_parses_datetime_format(self):
        result = parse_date_input("May 8, 2026-7:00 PM")
        self.assertEqual(result, datetime(2026, 5, 8, 19, 0))
        self.assertIsInstance(result, datetime)

    def test_parses_date_only_format(self):
        result = parse_date_input("May 8, 2026")
        self.assertEqual(result, date(2026, 5, 8))
        self.assertNotIsInstance(result, datetime)

    def test_raises_on_invalid_input(self):
        with self.assertRaises(ValueError):
            parse_date_input("not a date")


class TestParseUrlFromHtml(unittest.TestCase):
    def test_extracts_url_with_default_delimiters(self):
        html = '<a href="//foo.com/bar">link</a>'
        self.assertEqual(parse_url_from_html(html), "https://foo.com/bar")

    def test_unescapes_amp(self):
        html = '<a href="//foo.com/bar?a=1&amp;b=2">link</a>'
        self.assertEqual(parse_url_from_html(html), "https://foo.com/bar?a=1&b=2")

    def test_supports_custom_delimiters(self):
        html = "javascript:player('//foo.com/clip','args')"
        self.assertEqual(
            parse_url_from_html(html, start="//", end="','"),
            "https://foo.com/clip",
        )

    def test_returns_empty_when_no_match(self):
        self.assertEqual(parse_url_from_html("nothing"), "")


class TestParseMultipleUrlsFromHtml(unittest.TestCase):
    def test_extracts_named_urls(self):
        html = (
            '<option value="//foo.com/a">First Doc</option>'
            '<option value="//foo.com/b">Second Doc</option>'
        )
        result = parse_multiple_urls_from_html(html, start="//", end="</option>")
        self.assertEqual(
            result,
            {"First Doc": "https://foo.com/a", "Second Doc": "https://foo.com/b"},
        )

    def test_returns_empty_dict_when_no_match(self):
        self.assertEqual(
            parse_multiple_urls_from_html("nothing", start="//", end="</option>"),
            {},
        )


class TestGetInnerTextFromHtml(unittest.TestCase):
    def test_extracts_inner_text(self):
        self.assertEqual(get_inner_text_from_html("<span>hello</span>"), "hello")

    def test_returns_empty_when_no_tag(self):
        self.assertEqual(get_inner_text_from_html("plain text"), "")


class TestGetClipIdFromUrl(unittest.TestCase):
    def test_extracts_clip_id(self):
        url = "https://example.com/player?clip_id=12345&view_id=1"
        self.assertEqual(get_clip_id_from_url(url), "12345")

    def test_returns_none_when_no_clip_id(self):
        self.assertIsNone(get_clip_id_from_url("https://example.com/no-clip"))


if __name__ == "__main__":
    unittest.main()
