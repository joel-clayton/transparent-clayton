import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

from scripts import backfill_history as bf


class TestRunWiki(unittest.TestCase):
    def test_renders_the_wiki_floored_at_from_year(self):
        with mock.patch.object(bf, "WikiUpdater") as wiki:
            bf._run_wiki("src", 2019)
        wiki.assert_called_once_with("src")
        wiki.return_value.process.assert_called_once_with(datetime(2019, 1, 1))

    def test_wiki_stage_renders_without_odata_ingest(self):
        # The real contract: --stage wiki renders from persisted Redis detail and
        # never ingests from OData (which would pull in un-archived meetings whose
        # expiring source URLs would be rendered). Exercised through main().
        argv = [
            "backfill_history.py",
            "--stage",
            "wiki",
            "--type",
            "bac_mtg",
            "--execute",
            "--from-year",
            "2026",
            "--to-year",
            "2026",
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(bf, "_ingest_window") as ingest,
            mock.patch.object(bf, "_detail_meetings_in_range", return_value=[]),
            mock.patch.object(bf, "_run_wiki") as run_wiki,
            mock.patch.object(bf, "_run_docs") as run_docs,
            mock.patch.object(bf, "_run_video") as run_video,
        ):
            bf.main()
        ingest.assert_not_called()  # no OData ingest for the wiki stage
        run_wiki.assert_called_once()
        run_docs.assert_not_called()
        run_video.assert_not_called()


class TestRunSummaries(unittest.TestCase):
    def test_summarizes_then_renders_wiki_floored_at_from_year(self):
        with (
            mock.patch.object(bf, "RagIndexer") as rag,
            mock.patch.object(bf, "WikiUpdater") as wiki,
            mock.patch.object(bf, "DocumentDownloader") as dl,
        ):
            bf._run_summaries("src", 2019)
        floor = datetime(2019, 1, 1)
        rag.assert_called_once_with("src")
        rag.return_value.process.assert_called_once_with(floor)
        wiki.assert_called_once_with("src")
        wiki.return_value.process.assert_called_once_with(floor)
        dl.assert_not_called()  # disk-driven: no document download

    def test_summaries_stage_runs_without_odata_ingest(self):
        # --stage summaries is disk-driven: it summarizes transcribed meetings and
        # must not ingest from OData. Exercised through main().
        argv = [
            "backfill_history.py",
            "--stage",
            "summaries",
            "--type",
            "cc_mtg",
            "--execute",
        ]
        fake_idx = mock.Mock()
        fake_idx.get_most_recent_missing_dates.return_value = []
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(bf, "_ingest_window") as ingest,
            mock.patch.object(bf, "RagIndexer", return_value=fake_idx),
            mock.patch.object(bf, "_run_summaries") as run_summaries,
            mock.patch.object(bf, "_run_docs") as run_docs,
            mock.patch.object(bf, "_run_video") as run_video,
            mock.patch.object(bf, "_run_wiki") as run_wiki,
        ):
            bf.main()
        ingest.assert_not_called()  # no OData ingest for the summaries stage
        run_summaries.assert_called_once()
        run_docs.assert_not_called()
        run_video.assert_not_called()
        run_wiki.assert_not_called()


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
