import unittest
from unittest.mock import patch

from src.meeting_types import CITY_COUNCIL
from src.processors.download_docs import DocumentDownloader


class TestDocumentDownloader(unittest.TestCase):
    MEETINGS = [
        ("2026-06-02 07_00 PM", {"agenda_packet": "u1"}),
        ("2026-06-09 07_00 PM", {"agenda_packet": "u2"}),
    ]

    def test_saves_each_document_and_continues_past_a_failure(self):
        saved = []

        def ensure(meeting_type, meeting_key, label, url):
            if url == "u1":
                raise RuntimeError("boom")
            saved.append((meeting_key, url))
            return "/path"

        with (
            patch(
                "src.processors.download_docs.meetings_with_documents",
                return_value=iter(self.MEETINGS),
            ),
            patch(
                "src.processors.download_docs.ensure_document_on_disk",
                side_effect=ensure,
            ),
        ):
            DocumentDownloader(CITY_COUNCIL).process()

        # The failing document didn't abort the second meeting's download.
        self.assertEqual(saved, [("2026-06-09 07_00 PM", "u2")])


if __name__ == "__main__":
    unittest.main()
