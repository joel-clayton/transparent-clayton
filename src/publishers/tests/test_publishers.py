import unittest
from unittest.mock import patch

from src import settings
from src.publishers import PUBLISHERS, is_destination_enabled, run_publisher
from src.types import MEETING_TYPE_BY_SOURCE

TYPE_COUNT = len(MEETING_TYPE_BY_SOURCE)


class TestRegistry(unittest.TestCase):
    def test_registry_maps_stage_names_to_destinations(self):
        self.assertEqual(PUBLISHERS["upload_video"].destination, "youtube")
        self.assertEqual(PUBLISHERS["upload_transcript"].destination, "google_docs")
        self.assertEqual(PUBLISHERS["upload_docs"].destination, "google_docs")
        self.assertEqual(PUBLISHERS["update_wiki"].destination, "wiki")


class TestIsDestinationEnabled(unittest.TestCase):
    def test_enabled_by_default(self):
        with patch.object(settings, "DISABLED_PUBLISHERS", set()):
            self.assertTrue(is_destination_enabled("youtube"))
            self.assertTrue(is_destination_enabled("wiki"))

    def test_disabled_when_listed(self):
        with patch.object(settings, "DISABLED_PUBLISHERS", {"wiki"}):
            self.assertFalse(is_destination_enabled("wiki"))
            self.assertTrue(is_destination_enabled("youtube"))


class TestRunPublisherDispatch(unittest.TestCase):
    def test_runs_processor_per_meeting_type_when_enabled(self):
        with (
            patch.object(settings, "DISABLED_PUBLISHERS", set()),
            patch("src.publishers.registry.VideoUploader") as uploader,
        ):
            run_publisher("upload_video")
        self.assertEqual(uploader.call_count, TYPE_COUNT)
        self.assertEqual(uploader.return_value.process.call_count, TYPE_COUNT)

    def test_skips_processor_when_destination_disabled(self):
        with (
            patch.object(settings, "DISABLED_PUBLISHERS", {"youtube"}),
            patch("src.publishers.registry.VideoUploader") as uploader,
        ):
            run_publisher("upload_video")
        uploader.assert_not_called()

    def test_disabling_google_docs_skips_both_transcript_and_documents(self):
        with (
            patch.object(settings, "DISABLED_PUBLISHERS", {"google_docs"}),
            patch("src.publishers.registry.TranscriptUploader") as transcript,
            patch("src.publishers.registry.DocumentUploader") as archiver,
        ):
            run_publisher("upload_transcript")
            run_publisher("upload_docs")
        transcript.assert_not_called()
        archiver.assert_not_called()

    def test_document_archiver_runs_once_per_meeting_type(self):
        with (
            patch.object(settings, "DISABLED_PUBLISHERS", set()),
            patch("src.publishers.registry.DocumentUploader") as archiver,
        ):
            run_publisher("upload_docs")
        self.assertEqual(archiver.call_count, TYPE_COUNT)
        self.assertEqual(archiver.return_value.process.call_count, TYPE_COUNT)


if __name__ == "__main__":
    unittest.main()
