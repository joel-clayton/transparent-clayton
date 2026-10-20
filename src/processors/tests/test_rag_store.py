import os
import tempfile
import unittest

from src.processors.helpers.rag_store import (
    SectionRow,
    connect,
    replace_meeting_sections,
    search,
    section_row_to_json,
)


def _row(idx, summary, embedding=None, procedural=False, **kw):
    return SectionRow(
        meeting_type="City Council",
        meeting_key="2026-10-06 07_00 PM",
        item_index=idx,
        agenda_item=summary[:20],
        summary=summary,
        embedding=embedding,
        procedural=procedural,
        embed_model="nomic" if embedding else None,
        **kw,
    )


class TestRagStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "nested", "index.db")
        self.conn = connect(self.db)
        self.addCleanup(self.conn.close)

    def test_creates_parent_dir_and_schema(self):
        self.assertTrue(os.path.isdir(os.path.dirname(self.db)))

    def test_insert_search_and_procedural_unembedded(self):
        rows = [
            _row(5, "open space conservation", embedding=[1.0, 0.0, 0.0]),
            _row(6, "budget report", embedding=[0.0, 1.0, 0.0]),
            _row(1, "Roll call at [0:00:19].", procedural=True),  # no embedding
        ]
        replace_meeting_sections(self.conn, "City Council", "2026-10-06 07_00 PM", rows)
        self.assertEqual(
            self.conn.execute("select count(*) from sections").fetchone()[0], 3
        )
        # only the two embedded rows are searchable
        self.assertEqual(
            self.conn.execute("select count(*) from vec_sections").fetchone()[0], 2
        )
        hits = search(self.conn, [1.0, 0.0, 0.0], k=2)
        self.assertEqual(hits[0]["item_index"], 5)
        self.assertIsInstance(hits[0]["keywords"], list)

    def test_replace_is_idempotent(self):
        rows = [_row(5, "a", embedding=[1.0, 0.0, 0.0])]
        for _ in range(3):
            replace_meeting_sections(
                self.conn, "City Council", "2026-10-06 07_00 PM", rows
            )
        self.assertEqual(
            self.conn.execute("select count(*) from sections").fetchone()[0], 1
        )
        self.assertEqual(
            self.conn.execute("select count(*) from vec_sections").fetchone()[0], 1
        )

    def test_search_scoped_by_meeting_type(self):
        replace_meeting_sections(
            self.conn,
            "City Council",
            "k1",
            [_row(5, "cc", embedding=[1.0, 0.0, 0.0])],
        )
        rows2 = [
            SectionRow(
                meeting_type="Planning Commission",
                meeting_key="k2",
                item_index=2,
                agenda_item="pc",
                summary="pc",
                embedding=[0.9, 0.1, 0.0],
                embed_model="nomic",
            )
        ]
        replace_meeting_sections(self.conn, "Planning Commission", "k2", rows2)
        hits = search(
            self.conn, [1.0, 0.0, 0.0], k=5, meeting_type="Planning Commission"
        )
        self.assertTrue(all(h["meeting_type"] == "Planning Commission" for h in hits))

    def test_section_row_to_json_drops_embedding(self):
        d = section_row_to_json(_row(5, "a", embedding=[1.0, 0.0, 0.0]))
        self.assertNotIn("embedding", d)
        self.assertEqual(d["item_index"], 5)


if __name__ == "__main__":
    unittest.main()
