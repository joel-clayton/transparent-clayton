import unittest
from unittest.mock import patch

from src.meeting_types import CITY_COUNCIL
from src.processors.download_docs import DocumentDownloader


class TestDocumentDownloader(unittest.TestCase):
    MEETINGS = [
        ("2026-06-02 07_00 PM", {"agenda_packet": "u1"}),
        ("2026-06-09 07_00 PM", {"agenda_packet": "u2"}),
    ]

    def _run(self, ensure):
        with (
            patch(
                "src.processors.download_docs.meetings_with_documents",
                return_value=iter(self.MEETINGS),
            ),
            patch(
                "src.processors.download_docs.ensure_document_on_disk",
                side_effect=ensure,
            ),
            patch("src.processors.download_docs.alert") as alert,
        ):
            DocumentDownloader(CITY_COUNCIL).process()
        return alert

    def test_saves_each_document_and_continues_past_a_failure(self):
        saved = []

        def ensure(meeting_type, meeting_key, label, url):
            if url == "u1":
                raise RuntimeError("boom")
            saved.append((meeting_key, url))
            return "/path"

        alert = self._run(ensure)

        # The failing document didn't abort the second meeting's download,
        self.assertEqual(saved, [("2026-06-09 07_00 PM", "u2")])
        # and the failure is surfaced in one batched alert.
        alert.assert_called_once()
        self.assertIn("2026-06-02 07_00 PM", alert.call_args.args[1])

    def test_no_alert_when_every_document_saves(self):
        alert = self._run(lambda *a: "/path")
        alert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
