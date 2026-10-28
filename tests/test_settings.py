import importlib
import os
import unittest
from datetime import datetime
from unittest import mock

from src import settings
from src.processors import constants as processor_constants


class TestPipelineStart(unittest.TestCase):
    def test_default_and_env_override_and_shared_floor(self):
        # The single live-pipeline/backfill boundary: defaults to 2025-06-01, is
        # overridable by ISO date, and is the processors' default floor.
        self.assertEqual(settings.PIPELINE_START, datetime(2025, 6, 1))
        self.assertEqual(processor_constants.EARLIEST, settings.PIPELINE_START)
        try:
            with mock.patch.dict(os.environ, {"PIPELINE_START": "2026-03-01"}):
                importlib.reload(settings)
                self.assertEqual(settings.PIPELINE_START, datetime(2026, 3, 1))
        finally:
            importlib.reload(settings)


if __name__ == "__main__":
    unittest.main()
