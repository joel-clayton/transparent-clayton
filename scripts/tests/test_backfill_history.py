import unittest
from datetime import datetime

from scripts import backfill_history as bf


class TestYearWindows(unittest.TestCase):
    def test_windows_are_per_year_and_clamped_to_cutover(self):
        windows = bf._year_windows(2024, bf.CUTOVER.year)
        # first window starts at the requested year
        self.assertEqual(windows[0][0], datetime(2024, 1, 1))
        # last window ends exactly at the cutover, never past it
        self.assertEqual(windows[-1][1], bf.CUTOVER)
        self.assertTrue(all(start < end for start, end in windows))
        # each window is within a single calendar year
        for start, end in windows:
            self.assertLessEqual(end.year - start.year, 1)

    def test_empty_when_range_is_entirely_post_cutover(self):
        self.assertEqual(bf._year_windows(2027, 2028), [])


if __name__ == "__main__":
    unittest.main()
