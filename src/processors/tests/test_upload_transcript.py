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


class TestRetrieveAndStoreFilesInFolder(unittest.TestCase):
    """The function lists transcript files in a Drive folder AND stores each
    file's webViewLink in Redis under TRANSCRIPT_LINK_CC_MTG_KEY_TEMPLATE,
    keyed by the date/datetime parsed from the filename.
    """

    def setUp(self):
        self.uploader = _make_uploader()
        self.r_patcher = patch("src.processors.upload_transcript.r")
        self.mock_r = self.r_patcher.start()
        self.addCleanup(self.r_patcher.stop)

    def _stub_drive_response(self, files):
        """files: list of (name, webViewLink) tuples."""
        drive_files = [
            {"id": str(i), "name": n, "mimeType": "x", "webViewLink": link}
            for i, (n, link) in enumerate(files)
        ]
        execute = MagicMock(return_value={"files": drive_files, "nextPageToken": None})
        list_request = MagicMock()
        list_request.execute = execute
        files_resource = MagicMock()
        files_resource.list = MagicMock(return_value=list_request)
        self.uploader.service = MagicMock()
        self.uploader.service.files = MagicMock(return_value=files_resource)

    def test_extracts_date_only_filename(self):
        self._stub_drive_response(
            [("City Council Meeting 2026-05-08", "https://drive/abc")]
        )
        self.assertEqual(
            self.uploader.retrieve_and_store_files_in_folder("folder_id"),
            ["2026-05-08"],
        )
        self.mock_r.set.assert_called_once_with(
            "transcript_link.cc_mtg.2026-05-08", "https://drive/abc"
        )

    def test_normalizes_datetime_filename_colon_to_underscore(self):
        self._stub_drive_response(
            [("City Council Meeting 2026-05-08 07:00 PM", "https://drive/xyz")]
        )
        self.assertEqual(
            self.uploader.retrieve_and_store_files_in_folder("folder_id"),
            ["2026-05-08 07_00 PM"],
        )
        self.mock_r.set.assert_called_once_with(
            "transcript_link.cc_mtg.2026-05-08 07_00 PM", "https://drive/xyz"
        )

    def test_handles_mixed_filenames(self):
        self._stub_drive_response(
            [
                ("City Council Meeting 2026-05-08", "https://drive/date-only"),
                (
                    "City Council Meeting 2026-06-01 07:00 PM",
                    "https://drive/datetime",
                ),
            ]
        )
        self.assertEqual(
            sorted(self.uploader.retrieve_and_store_files_in_folder("folder_id")),
            ["2026-05-08", "2026-06-01 07_00 PM"],
        )
        self.assertEqual(
            sorted(
                (args.args[0], args.args[1]) for args in self.mock_r.set.call_args_list
            ),
            [
                ("transcript_link.cc_mtg.2026-05-08", "https://drive/date-only"),
                (
                    "transcript_link.cc_mtg.2026-06-01 07_00 PM",
                    "https://drive/datetime",
                ),
            ],
        )

    def test_skips_files_with_no_parseable_date(self):
        self._stub_drive_response([("Random Other File", "https://drive/skip")])
        self.assertEqual(
            self.uploader.retrieve_and_store_files_in_folder("folder_id"), []
        )
        self.mock_r.set.assert_not_called()


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
