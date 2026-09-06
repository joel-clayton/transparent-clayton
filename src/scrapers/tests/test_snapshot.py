import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from src.scrapers.errors import TransientScrapeError
from src.scrapers.snapshot import SnapshotStore, fetch_stamp


class TestFetchStamp(unittest.TestCase):
    def test_formats_a_filesystem_safe_stamp(self):
        stamp = fetch_stamp(datetime(2026, 9, 2, 14, 5, 30))
        self.assertEqual(stamp, "2026-09-02T140530")


class TestSnapshotStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # Mimic ".../CC Meetings/RawSnapshots": the parent must already exist,
        # standing in for the mounted volume.
        self.volume = Path(self._tmp.name) / "CC Meetings"
        self.volume.mkdir()
        self.root = self.volume / "RawSnapshots"
        self.store = SnapshotStore(self.root)

    def test_ensure_ready_creates_leaf_when_volume_present(self):
        self.store.ensure_ready()
        self.assertTrue(self.root.is_dir())

    def test_ensure_ready_raises_when_volume_missing(self):
        store = SnapshotStore(Path(self._tmp.name) / "Not Mounted" / "RawSnapshots")
        with self.assertRaises(TransientScrapeError):
            store.ensure_ready()
        # Must NOT have created anything under the missing mount point.
        self.assertFalse((Path(self._tmp.name) / "Not Mounted").exists())

    def test_write_and_read_meeting_file(self):
        self.store.ensure_ready()
        ref = self.store.write(
            "42", "2026-09-02T140530", "files.html", "<html>hi</html>"
        )
        self.assertEqual(ref, "42/2026-09-02T140530")
        self.assertEqual(self.store.read(ref, "files.html"), "<html>hi</html>")
        self.assertEqual(
            self.store.path_for(ref, "files.html"),
            self.root / "42" / "2026-09-02T140530" / "files.html",
        )

    def test_write_listing(self):
        self.store.ensure_ready()
        rel = self.store.write_listing("2026-09-02T140530", "<html>list</html>")
        self.assertEqual(rel, "_listing/2026-09-02T140530.html")
        self.assertEqual(
            (self.root / rel).read_text(encoding="utf-8"), "<html>list</html>"
        )


if __name__ == "__main__":
    unittest.main()
