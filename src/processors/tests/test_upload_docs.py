import logging
import unittest
from unittest.mock import MagicMock, mock_open, patch

from src.meeting_types import CITY_COUNCIL
from src.processors.upload_docs import DocumentUploader, docs_to_archive


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
        archiver = DocumentUploader.__new__(DocumentUploader)
        archiver.meeting_type = CITY_COUNCIL
        archiver.service = MagicMock()
        archiver.service.files.return_value.create.return_value.execute.return_value = {
            "webViewLink": "https://drive/view"
        }
        archiver._find_file = lambda folder_id, label: None  # not yet on Drive

        with (
            patch(
                "src.processors.upload_docs.ensure_document_on_disk",
                return_value="/docs/Exhibit A.png",
            ),
            patch("src.processors.upload_docs.open", mock_open(read_data=b"PNGDATA")),
            patch("src.processors.upload_docs.MediaIoBaseUpload") as media,
        ):
            link = archiver._archive_one("FOLDER", "2026-06-02", "Exhibit A", "u")

        self.assertEqual(link, "https://drive/view")
        # Uploaded with the type derived from the file, not a hardcoded PDF.
        self.assertEqual(media.call_args.kwargs["mimetype"], "image/png")


class TestFindFileReusesById(unittest.TestCase):
    """A retry must reuse an already-uploaded file (by id) rather than re-creating
    it — even when the listing omits webViewLink — so it never duplicates (TRA-154)."""

    def _archiver(self, listed):
        archiver = DocumentUploader.__new__(DocumentUploader)
        archiver.meeting_type = CITY_COUNCIL
        archiver.service = MagicMock()
        archiver.service.files.return_value.list.return_value.execute.return_value = {
            "files": listed
        }
        return archiver

    def test_returns_web_view_link_when_present(self):
        archiver = self._archiver([{"id": "f1", "webViewLink": "https://drive/view"}])
        self.assertEqual(
            archiver._find_file("FOLDER", "Exhibit A"), "https://drive/view"
        )

    def test_constructs_link_when_listing_omits_it(self):
        # Found-but-linkless must NOT look like not-found (which would re-create).
        archiver = self._archiver([{"id": "f2"}])
        self.assertEqual(
            archiver._find_file("FOLDER", "Exhibit A"),
            "https://drive.google.com/file/d/f2/view",
        )

    def test_none_when_not_found(self):
        self.assertIsNone(self._archiver([])._find_file("FOLDER", "Exhibit A"))

    def test_archive_one_reuses_without_recreating(self):
        archiver = DocumentUploader.__new__(DocumentUploader)
        archiver.meeting_type = CITY_COUNCIL
        archiver.service = MagicMock()
        archiver._find_file = lambda folder, label: (
            "https://drive.google.com/file/d/f2/view"
        )
        link = archiver._archive_one("FOLDER", "2026-06-02", "Exhibit A", "u")
        self.assertEqual(link, "https://drive.google.com/file/d/f2/view")
        archiver.service.files.return_value.create.assert_not_called()  # no duplicate


class TestArchiveMeetingMarksArchivedOnlyWhenComplete(unittest.TestCase):
    """A meeting is added to DOCS_ARCHIVED only when every document got a link;
    a partial/zero result leaves it unarchived so the next run retries — otherwise
    reconcile reports MISSING_DOC forever with no recovery path (TRA-154)."""

    DETAIL = {
        "agenda_packet": "https://x/packet.pdf",
        "minutes_and_supplemental_materials": {"Staff Report": "https://x/s.pdf"},
    }

    def _archiver(self):
        archiver = DocumentUploader.__new__(DocumentUploader)
        archiver.meeting_type = CITY_COUNCIL
        archiver.logger = logging.getLogger("test")
        archiver._ensure_meeting_folder = lambda key: "FOLDER"
        return archiver

    def test_all_succeed_marks_archived(self):
        archiver = self._archiver()
        archiver._archive_one = lambda folder, key, label, url: f"link:{label}"
        with patch("src.processors.upload_docs.r") as r:
            archiver._archive_meeting("2026-06-02", self.DETAIL)
        r.sadd.assert_called_once()

    def test_soft_failure_leaves_unarchived_for_retry(self):
        archiver = self._archiver()
        # One document returns "" (create succeeded but no link) — not a raise.
        archiver._archive_one = lambda folder, key, label, url: (
            "" if label == "Staff Report" else f"link:{label}"
        )
        with patch("src.processors.upload_docs.r") as r:
            archiver._archive_meeting("2026-06-02", self.DETAIL)
        r.sadd.assert_not_called()  # retried next run


if __name__ == "__main__":
    unittest.main()
