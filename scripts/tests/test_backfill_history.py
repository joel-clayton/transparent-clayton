import unittest
from datetime import datetime, timedelta
from unittest import mock

from scripts import backfill_history as bf


class TestRunWiki(unittest.TestCase):
    def test_renders_only_the_wiki_at_the_backfill_floor(self):
        with (
            mock.patch.object(bf, "WikiUpdater") as wiki,
            mock.patch.object(bf, "DocumentDownloader") as dl,
            mock.patch.object(bf, "DocumentUploader") as ul,
        ):
            bf._run_wiki("src")
        wiki.assert_called_once_with("src")
        wiki.return_value.process.assert_called_once_with(bf.BACKFILL_FLOOR)
        # wiki-only: no document download / upload, no A/V
        dl.assert_not_called()
        ul.assert_not_called()


class TestYearWindows(unittest.TestCase):
    def test_windows_are_per_year_and_clamped_to_cutover(self):
        windows = bf._year_windows(2024, bf.CUTOVER.year)
        # first window starts a microsecond before the requested year (so a Jan 1
        # 00:00:00 meeting isn't lost to the scrape's strict `start < dt` bound)
        self.assertEqual(
            windows[0][0], datetime(2024, 1, 1) - timedelta(microseconds=1)
        )
        # last window ends exactly at the cutover, never past it
        self.assertEqual(windows[-1][1], bf.CUTOVER)
        self.assertTrue(all(start < end for start, end in windows))
        # each window spans about one year
        for start, end in windows:
            self.assertLessEqual(end - start, timedelta(days=367))

    def test_empty_when_range_is_entirely_post_cutover(self):
        self.assertEqual(bf._year_windows(2027, 2028), [])


if __name__ == "__main__":
    unittest.main()
