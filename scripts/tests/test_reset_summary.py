import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock

from scripts import reset_summary as rs
from src.processors.helpers import rag_store

TYPE = "Budget and Audit Committee"
KEY = "2024-07-10 04_00 PM"
LEDGER = "summarized.bac_mtg"


def _delete_rows(conn, meeting_type, key):
    conn.execute(
        "DELETE FROM sections WHERE meeting_type=? AND meeting_key=?",
        (meeting_type, key),
    )


class TestResetSummary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = os.path.join(self.tmp.name, "m.md")
        self.sj = os.path.join(self.tmp.name, "m.sections.json")
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(rag_store._SECTIONS_DDL)
        for i, key in enumerate([KEY, KEY, "2024-08-12 04_00 PM"]):
            self.conn.execute(
                "INSERT INTO sections (meeting_type, meeting_key, item_index, "
                "agenda_item, summary) VALUES (?, ?, ?, 'x', 'y')",
                (TYPE, key, i),
            )
        self.redis = MagicMock()
        self.redis.hexists.return_value = True

    def _plan(self):
        return rs.plan_reset(KEY, self.md, self.sj, self.conn, TYPE, self.redis, LEDGER)

    def _touch(self, *paths):
        for path in paths:
            open(path, "w").close()

    def test_plan_then_apply_removes_files_rows_and_ledger_entry_only_for_the_meeting(
        self,
    ):
        self._touch(self.md, self.sj)
        plan = self._plan()
        self.assertEqual(plan.files, [self.md, self.sj])
        self.assertEqual(plan.rag_rows, 2)  # not the other meeting's row
        self.assertTrue(plan.in_ledger)
        # planning alone changes nothing
        self.assertTrue(os.path.exists(self.md))
        self.redis.hdel.assert_not_called()

        rs.apply_reset(plan, self.conn, TYPE, self.redis, LEDGER, _delete_rows)

        self.assertFalse(os.path.exists(self.md) or os.path.exists(self.sj))
        left = self.conn.execute("SELECT meeting_key FROM sections").fetchall()
        self.assertEqual(left, [("2024-08-12 04_00 PM",)])
        self.redis.hdel.assert_called_once_with(LEDGER, KEY)

    def test_legacy_summary_without_sections_json_is_left_alone(self):
        self._touch(self.md)  # an older generator's summary: no sections.json
        plan = self._plan()
        self.assertTrue(plan.legacy_only)
        self.assertEqual(plan.files, [])

    def test_nothing_to_do_when_no_files_rows_or_ledger_entry(self):
        self.conn.execute("DELETE FROM sections")
        self.redis.hexists.return_value = False
        self.assertTrue(self._plan().nothing_to_do)


if __name__ == "__main__":
    unittest.main()
