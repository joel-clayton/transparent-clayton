import unittest
from unittest.mock import MagicMock

from src.meeting_types import CITY_COUNCIL
from src.processors import drive_folders


class TestFindOrCreateTypeFolder(unittest.TestCase):
    def setUp(self):
        drive_folders._cache.clear()

    def _service(self, existing_files, created_id="NEW"):
        svc = MagicMock()
        svc.files.return_value.list.return_value.execute.return_value = {
            "files": existing_files
        }
        svc.files.return_value.create.return_value.execute.return_value = {
            "id": created_id
        }
        return svc

    def test_returns_existing_folder_without_creating(self):
        svc = self._service([{"id": "F1"}])
        fid = drive_folders.find_or_create_type_folder(svc, CITY_COUNCIL, parent_id="P")
        self.assertEqual(fid, "F1")
        svc.files.return_value.create.assert_not_called()

    def test_creates_folder_when_absent(self):
        svc = self._service([], created_id="F2")
        fid = drive_folders.find_or_create_type_folder(svc, CITY_COUNCIL, parent_id="P")
        self.assertEqual(fid, "F2")
        svc.files.return_value.create.assert_called_once()

    def test_second_lookup_is_cached(self):
        svc = self._service([{"id": "F1"}])
        drive_folders.find_or_create_type_folder(svc, CITY_COUNCIL, parent_id="P")
        svc.files.return_value.list.reset_mock()
        fid = drive_folders.find_or_create_type_folder(svc, CITY_COUNCIL, parent_id="P")
        self.assertEqual(fid, "F1")
        svc.files.return_value.list.assert_not_called()


if __name__ == "__main__":
    unittest.main()
