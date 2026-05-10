import unittest
from unittest.mock import MagicMock, patch

from src.processors.tests._helpers import make_uploader
from src.processors.upload_transcript import TranscriptUploader


def _make_uploader():
    return make_uploader(TranscriptUploader, auth_return=MagicMock())


class TestGetYearFromDate(unittest.TestCase):
    def setUp(self):
        self.uploader = _make_uploader()

    def test_extracts_year(self):
        self.assertEqual(self.uploader.get_year_from_date("2026-05-08"), "2026")


class TestListFilesInFolderDateExtraction(unittest.TestCase):
    def setUp(self):
        self.uploader = _make_uploader()

    def _stub_drive_response(self, names):
        files = [
            {"id": str(i), "name": n, "mimeType": "x"} for i, n in enumerate(names)
        ]
        execute = MagicMock(return_value={"files": files, "nextPageToken": None})
        list_request = MagicMock()
        list_request.execute = execute
        files_resource = MagicMock()
        files_resource.list = MagicMock(return_value=list_request)
        self.uploader.service = MagicMock()
        self.uploader.service.files = MagicMock(return_value=files_resource)

    def test_extracts_date_only_filename(self):
        self._stub_drive_response(["City Council Meeting 2026-05-08"])
        self.assertEqual(
            self.uploader.list_files_in_folder("folder_id"), ["2026-05-08"]
        )

    def test_normalizes_datetime_filename_colon_to_underscore(self):
        self._stub_drive_response(["City Council Meeting 2026-05-08 07:00 PM"])
        self.assertEqual(
            self.uploader.list_files_in_folder("folder_id"),
            ["2026-05-08 07_00 PM"],
        )

    def test_handles_mixed_filenames(self):
        self._stub_drive_response(
            [
                "City Council Meeting 2026-05-08",
                "City Council Meeting 2026-06-01 07:00 PM",
            ]
        )
        self.assertEqual(
            sorted(self.uploader.list_files_in_folder("folder_id")),
            ["2026-05-08", "2026-06-01 07_00 PM"],
        )


class TestCreateFileFormatDispatch(unittest.TestCase):
    def setUp(self):
        self.uploader = _make_uploader()
        self.uploader.service = MagicMock()
        self.uploader.service.files.return_value.create.return_value.execute.return_value = {
            "id": "file_id_123"
        }
        self.uploader.service.permissions.return_value.create.return_value.execute.return_value = {
            "id": "perm_id"
        }

    def _captured_filename(self):
        create_kwargs = self.uploader.service.files.return_value.create.call_args.kwargs
        return create_kwargs["body"]["name"]

    @patch("src.processors.upload_transcript.MediaFileUpload")
    @patch("src.processors.upload_transcript.time.sleep", return_value=None)
    @patch("src.processors.upload_transcript.sleep", return_value=None)
    def test_date_only_input_omits_time_suffix(self, _sleep1, _sleep2, _media):
        self.uploader.create_file("parent_id", "2026-05-08")
        self.assertEqual(self._captured_filename(), "City Council Meeting 2026-05-08")

    @patch("src.processors.upload_transcript.MediaFileUpload")
    @patch("src.processors.upload_transcript.time.sleep", return_value=None)
    @patch("src.processors.upload_transcript.sleep", return_value=None)
    def test_datetime_input_includes_time_suffix(self, _sleep1, _sleep2, _media):
        self.uploader.create_file("parent_id", "2026-05-08 07_00 PM")
        self.assertEqual(
            self._captured_filename(),
            "City Council Meeting 2026-05-08 07:00 PM",
        )

    @patch("src.processors.upload_transcript.MediaFileUpload")
    def test_unparseable_input_raises(self, _media):
        with self.assertRaises(Exception):
            self.uploader.create_file("parent_id", "nothing parseable")


if __name__ == "__main__":
    unittest.main()
