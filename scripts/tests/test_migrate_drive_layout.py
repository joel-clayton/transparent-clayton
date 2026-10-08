import unittest
from unittest.mock import MagicMock, patch

import scripts.migrate_drive_layout as m
from src.meeting_types import CITY_COUNCIL

_FOLDER = "application/vnd.google-apps.folder"
_DOC = "application/vnd.google-apps.document"


class TestMeetingKeyFromDriveName(unittest.TestCase):
    def test_datetime_colon_normalized_to_underscore(self):
        # Transcript Doc names use a colon; the key/doc-folder use an underscore.
        self.assertEqual(
            m.meeting_key_from_drive_name("City Council Meeting 2026-05-26 07:00 PM"),
            "2026-05-26 07_00 PM",
        )

    def test_date_only(self):
        self.assertEqual(
            m.meeting_key_from_drive_name("City Council Meeting 2026-05-08"),
            "2026-05-08",
        )

    def test_none_when_no_date(self):
        self.assertIsNone(m.meeting_key_from_drive_name("Agenda Packet"))


class TestMigrateTypeAudit(unittest.TestCase):
    def test_counts_doc_folder_and_transcript_moves(self):
        svc = MagicMock()
        type_children = [
            {"id": "y26", "name": "2026", "mimeType": _FOLDER},
            # legacy doc folder at the type level
            {
                "id": "docf",
                "name": "City Council Meeting 2026-05-26 07_00 PM",
                "mimeType": _FOLDER,
            },
        ]
        year_children = [
            # a transcript Doc sitting directly in the year folder
            {
                "id": "t1",
                "name": "City Council Meeting 2026-05-26 07:00 PM",
                "mimeType": _DOC,
            },
            # an already-migrated per-meeting folder: skipped
            {
                "id": "m1",
                "name": "City Council Meeting 2026-06-02 07_00 PM",
                "mimeType": _FOLDER,
            },
        ]

        def fake_list(service, parent_id):
            if parent_id == "TYPE":
                return type_children
            if parent_id == "y26":
                return year_children
            return []

        with (
            patch.object(m, "find_or_create_type_folder", return_value="TYPE"),
            patch.object(m, "_list_children", side_effect=fake_list),
        ):
            folders, transcripts = m._migrate_type(svc, CITY_COUNCIL, execute=False)

        self.assertEqual((folders, transcripts), (1, 1))
        svc.files.return_value.update.assert_not_called()  # audit mode: no writes


if __name__ == "__main__":
    unittest.main()
