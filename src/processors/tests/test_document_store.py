import json
import os
import unittest
from unittest.mock import MagicMock, patch

from src.meeting_types import CITY_COUNCIL
from src.processors import document_store
from src.processors.tests._helpers import TempDirTestCase


class TestDocsToArchive(unittest.TestCase):
    def test_collects_agenda_packet_and_nonempty_minutes(self):
        docs = document_store.docs_to_archive(
            {
                "agenda_packet": "https://x/packet.pdf",
                "minutes_and_supplemental_materials": {
                    "Staff Report": "https://x/sr.pdf",
                    "Empty": "",
                },
            }
        )
        self.assertEqual(
            docs,
            {
                "Agenda Packet": "https://x/packet.pdf",
                "Staff Report": "https://x/sr.pdf",
            },
        )

    def test_empty_when_no_documents(self):
        self.assertEqual(
            document_store.docs_to_archive({"video": "https://x/v.mp4"}), {}
        )


class TestDocumentPath(unittest.TestCase):
    def test_path_is_per_meeting_and_sanitized(self):
        with patch.object(document_store, "DOCUMENTS_DIR", "/docs/"):
            path = document_store.document_path(
                CITY_COUNCIL, "2026-06-02 07_00 PM", "Staff Report/A"
            )
        self.assertEqual(
            path,
            os.path.join(
                "/docs/",
                "City Council Meeting 2026-06-02 07_00 PM",
                "Staff Report-A.pdf",
            ),
        )


class TestEnsureDocumentOnDisk(TempDirTestCase):
    def _patch_dir(self):
        return patch.object(document_store, "DOCUMENTS_DIR", self.tmpdir + os.sep)

    def test_downloads_when_absent_then_is_idempotent(self):
        response = MagicMock(content=b"%PDF-1.4 data")
        response.raise_for_status = MagicMock()
        with (
            self._patch_dir(),
            patch(
                "src.processors.document_store.requests.get", return_value=response
            ) as get,
        ):
            path1 = document_store.ensure_document_on_disk(
                CITY_COUNCIL, "2026-06-02 07_00 PM", "Agenda Packet", "https://x/a.pdf"
            )
            # Second call must not re-download.
            path2 = document_store.ensure_document_on_disk(
                CITY_COUNCIL, "2026-06-02 07_00 PM", "Agenda Packet", "https://x/a.pdf"
            )

        self.assertEqual(path1, path2)
        self.assertTrue(os.path.exists(path1))
        with open(path1, "rb") as handle:
            self.assertEqual(handle.read(), b"%PDF-1.4 data")
        get.assert_called_once()  # downloaded exactly once
        # No leftover temp file.
        self.assertFalse(os.path.exists(path1 + ".part"))


class TestMeetingsWithDocuments(unittest.TestCase):
    def test_yields_only_archivable_meetings_that_have_docs(self):
        details = {
            b"a": json.dumps(
                {"key": "a", "pipeline_class": "full", "agenda_packet": "u1"}
            ).encode(),
            b"b": json.dumps(
                {"key": "b", "pipeline_class": "no_assets", "agenda_packet": "u2"}
            ).encode(),
            b"c": json.dumps({"key": "c", "pipeline_class": "docs_only"}).encode(),
        }
        with patch("src.processors.document_store.r") as r:
            r.hgetall.return_value = details
            keys = [
                key for key, _ in document_store.meetings_with_documents(CITY_COUNCIL)
            ]
        self.assertEqual(keys, ["a"])  # b is no_assets, c has no docs


if __name__ == "__main__":
    unittest.main()
