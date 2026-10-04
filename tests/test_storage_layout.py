import os
import tempfile
import unittest
from unittest import mock

from src.storage_layout import (
    iter_stage_files,
    read_dirs_for_bucket,
    resolve_existing_file,
    write_dir_for_bucket,
)


class TestStorageLayout(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.base = td.name

    def test_write_dir_bootstraps_tree_when_volume_mounted(self):
        # Volume mounted (root exists) but the stage dir does not yet: first run
        # self-bootstraps the whole tree, including the stage dir and the bucket.
        stage = os.path.join(self.base, "Compressed")
        with mock.patch("src.storage_layout.STORAGE_ROOT", self.base):
            path = write_dir_for_bucket(stage, "City Council")
        self.assertEqual(path, os.path.join(stage, "City Council") + os.sep)
        self.assertTrue(os.path.isdir(path))

    def test_write_dir_does_not_create_when_volume_unmounted(self):
        # Unmounted-volume guard: never fabricate the stage tree on the boot disk.
        stage = os.path.join(self.base, "Compressed")
        unmounted = os.path.join(self.base, "not-mounted")  # STORAGE_ROOT absent
        with mock.patch("src.storage_layout.STORAGE_ROOT", unmounted):
            path = write_dir_for_bucket(stage, "City Council")
        self.assertEqual(path, os.path.join(stage, "City Council") + os.sep)
        self.assertFalse(os.path.exists(path))

    def test_iter_stage_files_spans_bucket_and_flat_and_skips_sidecars(self):
        bucket_dir = os.path.join(self.base, "City Council")
        os.makedirs(bucket_dir)
        for p in (
            os.path.join(bucket_dir, "bucketed.mp4"),
            os.path.join(self.base, "legacy_flat.mp4"),
            os.path.join(self.base, "._sidecar.mp4"),
        ):
            with open(p, "w"):
                pass
        found = {
            os.path.basename(p) for p in iter_stage_files(self.base, "City Council")
        }
        self.assertEqual(found, {"bucketed.mp4", "legacy_flat.mp4"})

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
