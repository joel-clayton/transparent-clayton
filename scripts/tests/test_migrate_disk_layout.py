import os
import tempfile
import unittest
from unittest.mock import patch

from scripts.migrate_disk_layout import main, type_for_name
from src.meeting_types import CITY_COUNCIL, GENERAL, PLANNING_COMMISSION


class TestTypeForName(unittest.TestCase):
    def test_matches_single_type_by_stub(self):
        self.assertIs(
            type_for_name("Clayton CA City Council Meeting 2026-05-08 - 000.mp4"),
            CITY_COUNCIL,
        )
        self.assertIs(
            type_for_name("General Meeting 2026-08-26 - City of Clayton.txt"),
            GENERAL,
        )

    def test_none_when_no_stub(self):
        self.assertIsNone(type_for_name("Some Unrelated File 2026-05-08.mp4"))


class TestMigrationMoves(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.root = self.td.name
        self.downloaded = os.path.join(self.root, "Downloaded") + os.sep
        self.documents = os.path.join(self.root, "Documents") + os.sep
        os.makedirs(self.downloaded)
        os.makedirs(self.documents)
        # Point the script's stage/documents dirs at the temp tree.
        patcher = patch.multiple(
            "scripts.migrate_disk_layout",
            STAGE_DIRS=[self.downloaded],
            DOCUMENTS_DIR=self.documents,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _touch(self, directory, name):
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, name), "w"):
            pass

    def _run(self, execute):
        argv = ["migrate", "--execute"] if execute else ["migrate"]
        with patch("sys.argv", argv):
            main()

    def test_execute_moves_flat_file_into_bucket(self):
        name = "Clayton CA Planning Commission Meeting 2026-05-26 - 000.mp4"
        self._touch(self.downloaded, name)
        self._run(execute=True)
        self.assertFalse(os.path.exists(os.path.join(self.downloaded, name)))
        self.assertTrue(
            os.path.exists(
                os.path.join(self.downloaded, PLANNING_COMMISSION.disk_bucket, name)
            )
        )

    def test_audit_moves_nothing(self):
        name = "Clayton CA City Council Meeting 2026-05-08 - 000.mp4"
        self._touch(self.downloaded, name)
        self._run(execute=False)
        self.assertTrue(os.path.exists(os.path.join(self.downloaded, name)))
        self.assertFalse(os.path.isdir(os.path.join(self.downloaded, "City Council")))

    def test_skips_appledouble_and_already_bucketed(self):
        self._touch(self.downloaded, "._sidecar City Council Meeting 2026-05-08.mp4")
        # An existing bucket dir with a file is not re-descended / re-moved.
        bucket = os.path.join(self.downloaded, "City Council")
        self._touch(bucket, "City Council Meeting 2026-05-08 - City of Clayton.mp4")
        self._run(execute=True)
        self.assertTrue(
            os.path.exists(
                os.path.join(
                    self.downloaded, "._sidecar City Council Meeting 2026-05-08.mp4"
                )
            )
        )
        # The bucketed file stays exactly where it was (not nested again).
        self.assertFalse(os.path.isdir(os.path.join(bucket, "City Council")))

    def test_does_not_overwrite_existing_destination(self):
        name = "Clayton CA City Council Meeting 2026-05-08 - 000.mp4"
        self._touch(self.downloaded, name)  # flat
        bucket = os.path.join(self.downloaded, "City Council")
        self._touch(bucket, name)  # destination already has this name
        self._run(execute=True)
        # The flat file is left in place (never clobbers the bucketed one).
        self.assertTrue(os.path.exists(os.path.join(self.downloaded, name)))

    def test_moves_document_folder_into_bucket(self):
        folder = "General Meeting 2026-08-26 06_00 PM"
        os.makedirs(os.path.join(self.documents, folder))
        self._touch(os.path.join(self.documents, folder), "Agenda Packet.pdf")
        self._run(execute=True)
        self.assertFalse(os.path.isdir(os.path.join(self.documents, folder)))
        self.assertTrue(
            os.path.isdir(os.path.join(self.documents, GENERAL.disk_bucket, folder))
        )


if __name__ == "__main__":
    unittest.main()
