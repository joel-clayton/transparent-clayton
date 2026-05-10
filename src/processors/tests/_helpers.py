import os
import tempfile
import unittest
from unittest.mock import patch


class TempDirTestCase(unittest.TestCase):
    """Provides self-cleaning tempdirs and a touch helper for filename-based tests."""

    def setUp(self):
        self.tmpdir = self.make_tmpdir()

    def make_tmpdir(self) -> str:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        return td.name

    def touch(self, name: str, dir_path: str | None = None) -> str:
        path = os.path.join(dir_path or self.tmpdir, name)
        with open(path, "w") as f:
            f.write("")
        return path


def make_uploader(cls, auth_return=None):
    with patch.object(cls, "authenticate", return_value=auth_return):
        return cls()
