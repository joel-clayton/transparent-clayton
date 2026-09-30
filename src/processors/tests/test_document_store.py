import json
import os
import unittest
from unittest.mock import MagicMock, patch

from src.meeting_types import CITY_COUNCIL
from src.processors.helpers import document_store
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


class TestExtensionAndMimetype(unittest.TestCase):
    def test_extension_from_content_type_then_url_then_pdf(self):
        self.assertEqual(document_store._extension_for("application/pdf", "u"), ".pdf")
        self.assertEqual(document_store._extension_for("image/png", "u"), ".png")
        # No/unknown Content-Type falls back to the URL extension, then .pdf.
        self.assertEqual(
            document_store._extension_for(None, "https://x/doc.docx"), ".docx"
        )
        self.assertEqual(document_store._extension_for(None, "https://x/blob"), ".pdf")

    def test_document_mimetype_from_extension(self):
        self.assertEqual(
            document_store.document_mimetype("/d/a.pdf"), "application/pdf"
        )
        self.assertEqual(document_store.document_mimetype("/d/a.png"), "image/png")


class TestEnsureDocumentOnDisk(TempDirTestCase):
    def _patch_dir(self):
        return patch.object(document_store, "DOCUMENTS_DIR", self.tmpdir + os.sep)

    def _response(self, content, content_type):
        response = MagicMock(content=content)
        response.raise_for_status = MagicMock()
        response.headers = {"Content-Type": content_type}
        return response

    def test_downloads_when_absent_then_is_idempotent(self):
        response = self._response(b"%PDF-1.4 data", "application/pdf")
        with (
            self._patch_dir(),
            patch(
                "src.processors.helpers.document_store.requests.get",
                return_value=response,
            ) as get,
        ):
            path1 = document_store.ensure_document_on_disk(
                CITY_COUNCIL, "2026-06-02 07_00 PM", "Agenda Packet", "https://x/a.pdf"
            )
            # Second call must not re-download (found on disk by label stem).
            path2 = document_store.ensure_document_on_disk(
                CITY_COUNCIL, "2026-06-02 07_00 PM", "Agenda Packet", "https://x/a.pdf"
            )

        self.assertEqual(path1, path2)
        self.assertTrue(path1.endswith("Agenda Packet.pdf"))
        with open(path1, "rb") as handle:
            self.assertEqual(handle.read(), b"%PDF-1.4 data")
        get.assert_called_once()  # downloaded exactly once
        self.assertFalse(os.path.exists(path1 + ".part"))  # no leftover temp file

    def test_extension_and_mimetype_follow_non_pdf_content_type(self):
        response = self._response(b"\x89PNG data", "image/png")
        with (
            self._patch_dir(),
            patch(
                "src.processors.helpers.document_store.requests.get",
                return_value=response,
            ),
        ):
            path = document_store.ensure_document_on_disk(
                CITY_COUNCIL, "2026-06-02 07_00 PM", "Exhibit A", "https://x/exhibit"
            )
        self.assertTrue(path.endswith("Exhibit A.png"))
        self.assertEqual(document_store.document_mimetype(path), "image/png")


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
        with patch("src.processors.helpers.document_store.r") as r:
            r.hgetall.return_value = details
            keys = [
                key for key, _ in document_store.meetings_with_documents(CITY_COUNCIL)
            ]
        self.assertEqual(keys, ["a"])  # b is no_assets, c has no docs


if __name__ == "__main__":
    unittest.main()
