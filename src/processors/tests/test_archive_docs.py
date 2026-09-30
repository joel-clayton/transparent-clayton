import unittest
from unittest.mock import MagicMock, mock_open, patch

from src.meeting_types import CITY_COUNCIL
from src.processors.archive_docs import DocumentArchiver, docs_to_archive


class TestDocsToArchive(unittest.TestCase):
    def test_collects_agenda_packet_and_nonempty_minutes(self):
        docs = docs_to_archive(
            {
                "agenda_packet": "https://x/packet.pdf",
                "minutes_and_supplemental_materials": {
                    "Staff Report": "https://x/s.pdf",
                    "Empty": "",
                },
            }
        )
        self.assertEqual(
            docs,
            {
                "Agenda Packet": "https://x/packet.pdf",
                "Staff Report": "https://x/s.pdf",
            },
        )

    def test_empty_when_no_documents(self):
        self.assertEqual(docs_to_archive({"video": "https://x/v.mp4"}), {})


class TestArchiveOneUploadsFromDisk(unittest.TestCase):
    def test_uploads_on_disk_bytes_with_content_derived_mimetype(self):
        archiver = DocumentArchiver.__new__(DocumentArchiver)
        archiver.meeting_type = CITY_COUNCIL
        archiver.service = MagicMock()
        archiver.service.files.return_value.create.return_value.execute.return_value = {
            "webViewLink": "https://drive/view"
        }
        archiver._find_file = lambda folder_id, label: None  # not yet on Drive

        with (
            patch(
                "src.processors.archive_docs.ensure_document_on_disk",
                return_value="/docs/Exhibit A.png",
            ),
            patch("src.processors.archive_docs.open", mock_open(read_data=b"PNGDATA")),
            patch("src.processors.archive_docs.MediaIoBaseUpload") as media,
        ):
            link = archiver._archive_one("FOLDER", "2026-06-02", "Exhibit A", "u")

        self.assertEqual(link, "https://drive/view")
        # Uploaded with the type derived from the file, not a hardcoded PDF.
        self.assertEqual(media.call_args.kwargs["mimetype"], "image/png")


if __name__ == "__main__":
    unittest.main()
