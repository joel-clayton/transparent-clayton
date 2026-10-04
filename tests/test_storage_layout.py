import os
import tempfile
import unittest

from src.storage_layout import (
    read_dirs_for_bucket,
    resolve_existing_file,
    write_dir_for_bucket,
)


class TestStorageLayout(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.base = td.name

    def test_write_dir_creates_and_returns_bucket_path(self):
        path = write_dir_for_bucket(self.base, "City Council")
        self.assertEqual(path, os.path.join(self.base, "City Council") + os.sep)
        self.assertTrue(os.path.isdir(path))

    def test_read_dirs_are_bucket_then_legacy_flat(self):
        self.assertEqual(
            read_dirs_for_bucket(self.base, "GHAD"),
            [os.path.join(self.base, "GHAD") + os.sep, self.base],
        )

    def test_read_dirs_does_not_create_anything(self):
        read_dirs_for_bucket(self.base, "General")
        self.assertFalse(os.path.isdir(os.path.join(self.base, "General")))

    def test_resolve_prefers_bucket_then_flat_then_defaults_to_bucket(self):
        name = "f.txt"
        bucket_path = os.path.join(self.base, "City Council", name)
        flat_path = os.path.join(self.base, name)

        # Neither exists -> defaults to the bucket path (for an imminent write).
        self.assertEqual(
            resolve_existing_file(self.base, "City Council", name), bucket_path
        )

        # Only the legacy flat file exists -> that path.
        with open(flat_path, "w"):
            pass
        self.assertEqual(
            resolve_existing_file(self.base, "City Council", name), flat_path
        )

        # Bucket file exists too -> bucket wins.
        os.makedirs(os.path.dirname(bucket_path))
        with open(bucket_path, "w"):
            pass
        self.assertEqual(
            resolve_existing_file(self.base, "City Council", name), bucket_path
        )


if __name__ == "__main__":
    unittest.main()
