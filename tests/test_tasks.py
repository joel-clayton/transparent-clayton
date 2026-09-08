import json
import unittest
from unittest import mock

import src.tasks as tasks
from src.meeting_types import CITY_COUNCIL, PLANNING_COMMISSION


def _meetings():
    return [
        {"key": "2026-06-03 07_00 PM", "pipeline_class": "full"},
        {"key": "2026-06-10 07_00 PM", "pipeline_class": "docs_only"},
    ]


class TestScrapeTypeForDownload(unittest.TestCase):
    def test_publishes_only_full_dates_to_type_scoped_worklist(self):
        with (
            mock.patch.object(
                tasks, "get_latest_downloaded_date", return_value="2026-05-01"
            ),
            mock.patch.object(
                tasks, "parse_meetings_from_url", return_value=_meetings()
            ),
            mock.patch.object(tasks, "r") as r,
        ):
            result = tasks._scrape_type_for_download(CITY_COUNCIL)

        key, payload = r.set.call_args.args
        self.assertEqual(key, "scraped.cc_mtg")
        # Only the FULL (video) meeting drives the A/V worklist.
        self.assertEqual(json.loads(payload), ["2026-06-03 07_00 PM"])
        self.assertEqual(result, _meetings())

    def test_new_type_falls_back_to_scrape_start(self):
        with (
            mock.patch.object(tasks, "get_latest_downloaded_date", return_value=""),
            mock.patch.object(
                tasks, "parse_meetings_from_url", return_value=[]
            ) as parse,
            mock.patch.object(tasks, "r") as r,
        ):
            tasks._scrape_type_for_download(PLANNING_COMMISSION)

        # A type with no downloads scrapes from the fallback watermark for its
        # own type, rather than being skipped.
        self.assertEqual(parse.call_args.args[0], tasks.NEW_TYPE_SCRAPE_START)
        self.assertIs(parse.call_args.args[1], PLANNING_COMMISSION)
        self.assertEqual(r.set.call_args.args[0], "scraped.pc_mtg")


if __name__ == "__main__":
    unittest.main()
