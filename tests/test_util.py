import unittest

from src.util import (
    get_date_or_datetime_string_from_string,
    get_date_string_from_string,
    get_datetime_string_from_string,
    get_year_string_from_string,
)


class TestGetDateStringFromString(unittest.TestCase):
    def test_extracts_iso_date(self):
        self.assertEqual(get_date_string_from_string("2026-05-08"), "2026-05-08")

    def test_extracts_date_embedded_in_filename(self):
        self.assertEqual(
            get_date_string_from_string("City Council Meeting 2026-05-08 - foo.mp4"),
            "2026-05-08",
        )

    def test_returns_first_date_when_multiple(self):
        self.assertEqual(
            get_date_string_from_string("2026-05-08 then 2026-06-01"),
            "2026-05-08",
        )

    def test_returns_empty_when_no_date(self):
        self.assertEqual(get_date_string_from_string("no date here"), "")

    def test_does_not_match_partial_date(self):
        self.assertEqual(get_date_string_from_string("2026-5-8"), "")


class TestGetDatetimeStringFromString(unittest.TestCase):
    def test_extracts_underscore_form(self):
        self.assertEqual(
            get_datetime_string_from_string("2026-05-08 07_00 PM"),
            "2026-05-08 07_00 PM",
        )

    def test_normalizes_colon_form_to_underscore(self):
        self.assertEqual(
            get_datetime_string_from_string("2026-05-08 07:00 PM"),
            "2026-05-08 07_00 PM",
        )

    def test_extracts_from_filename(self):
        self.assertEqual(
            get_datetime_string_from_string(
                "City Council Meeting 2026-05-08 07_00 PM - foo.mp4"
            ),
            "2026-05-08 07_00 PM",
        )

    def test_returns_empty_when_no_time_component(self):
        self.assertEqual(get_datetime_string_from_string("2026-05-08"), "")

    def test_returns_empty_for_no_match(self):
        self.assertEqual(get_datetime_string_from_string("nothing"), "")


class TestGetDateOrDatetimeStringFromString(unittest.TestCase):
    def test_prefers_datetime_when_present(self):
        self.assertEqual(
            get_date_or_datetime_string_from_string("2026-05-08 07_00 PM"),
            "2026-05-08 07_00 PM",
        )

    def test_falls_back_to_date(self):
        self.assertEqual(
            get_date_or_datetime_string_from_string("2026-05-08"),
            "2026-05-08",
        )

    def test_normalizes_colon_form(self):
        self.assertEqual(
            get_date_or_datetime_string_from_string("2026-05-08 07:00 PM"),
            "2026-05-08 07_00 PM",
        )

    def test_returns_empty_when_neither_matches(self):
        self.assertEqual(get_date_or_datetime_string_from_string("nothing"), "")


class TestGetYearStringFromString(unittest.TestCase):
    def test_extracts_four_digit_year(self):
        self.assertEqual(get_year_string_from_string("foo 2026 bar"), "2026")

    def test_does_not_match_three_digits(self):
        self.assertEqual(get_year_string_from_string("foo 999 bar"), "")

    def test_does_not_match_when_part_of_longer_run(self):
        self.assertEqual(get_year_string_from_string("foo20260bar"), "")

    def test_returns_empty_when_absent(self):
        self.assertEqual(get_year_string_from_string("no year"), "")


if __name__ == "__main__":
    unittest.main()
