import json
import unittest
from unittest import mock

import src.tasks as tasks
from src import settings
from src.meeting_types import CITY_COUNCIL, PLANNING_COMMISSION

ALWAYS_ON = {
    "src.tasks.get_cc_meeting_details_for_download",
    "src.tasks.download_cc_meeting_video",
    "src.tasks.compress_cc_meeting_video",
    "src.tasks.extract_cc_meeting_audio",
    "src.tasks.transcribe_cc_meeting_audio",
    "src.tasks.download_cc_meeting_docs",
    "src.tasks.notify_success",
}
PUBLISHING = {
    "src.tasks.upload_cc_meeting_video",
    "src.tasks.upload_cc_meeting_transcript",
    "src.tasks.archive_cc_meeting_docs",
    "src.tasks.update_cc_mtg_wiki",
}


def _stage_names(disabled):
    with mock.patch.object(settings, "DISABLED_PUBLISHERS", set(disabled)):
        return [sig.name for sig in tasks.build_workflow().tasks]


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


class TestBuildWorkflow(unittest.TestCase):
    def test_default_includes_every_stage_in_order(self):
        names = _stage_names(disabled=set())
        self.assertEqual(
            names,
            [
                "src.tasks.get_cc_meeting_details_for_download",
                "src.tasks.download_cc_meeting_video",
                "src.tasks.compress_cc_meeting_video",
                "src.tasks.upload_cc_meeting_video",
                "src.tasks.extract_cc_meeting_audio",
                "src.tasks.transcribe_cc_meeting_audio",
                "src.tasks.download_cc_meeting_docs",
                "src.tasks.upload_cc_meeting_transcript",
                "src.tasks.archive_cc_meeting_docs",
                "src.tasks.update_cc_mtg_wiki",
                "src.tasks.notify_success",
            ],
        )

    def test_disabling_youtube_drops_only_the_video_upload(self):
        names = set(_stage_names(disabled={"youtube"}))
        self.assertNotIn("src.tasks.upload_cc_meeting_video", names)
        self.assertIn("src.tasks.upload_cc_meeting_transcript", names)
        self.assertIn("src.tasks.update_cc_mtg_wiki", names)

    def test_disabling_google_docs_drops_transcript_and_documents(self):
        names = set(_stage_names(disabled={"google_docs"}))
        self.assertNotIn("src.tasks.upload_cc_meeting_transcript", names)
        self.assertNotIn("src.tasks.archive_cc_meeting_docs", names)
        self.assertIn("src.tasks.upload_cc_meeting_video", names)

    def test_disk_only_drops_all_publishing_stages(self):
        names = set(_stage_names(disabled={"all"}))
        self.assertEqual(names & PUBLISHING, set())
        self.assertEqual(names, ALWAYS_ON)


if __name__ == "__main__":
    unittest.main()
