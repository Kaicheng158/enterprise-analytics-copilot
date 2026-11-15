import contextlib
import io
import os
from unittest.mock import patch
import unittest

from pydantic import ValidationError

from backend.datasets import DatasetLifecycleSettings
from backend.main import app
from scripts.run_dataset_maintenance import main


class DatasetLifecycleOfflineTests(unittest.TestCase):
    def test_lifecycle_contract_is_strict_and_default_off(self):
        self.assertFalse(DatasetLifecycleSettings().enabled)
        with self.assertRaises(ValidationError):
            DatasetLifecycleSettings(enabled=True, unexpected=True)
        with self.assertRaises(ValidationError):
            DatasetLifecycleSettings(enabled=True, max_items_per_run=0)

    def test_manual_runner_fails_closed_before_database_or_storage_access(self):
        output = io.StringIO()
        environment = {key: value for key, value in os.environ.items() if key != "PRIVATE_DATASET_LIFECYCLE_ENABLED"}
        with patch.dict(os.environ, environment, clear=True), patch(
            "sys.argv", ["run_dataset_maintenance.py", "--trusted-local-synthetic"]
        ), contextlib.redirect_stderr(output):
            self.assertEqual(main(), 2)
        self.assertEqual(output.getvalue().strip(), "lifecycle_disabled")

    def test_existing_api_has_no_dataset_lifecycle_route(self):
        paths = {route.path for route in app.routes if hasattr(route, "path")}
        self.assertFalse(any("dataset" in path or "lifecycle" in path for path in paths))


if __name__ == "__main__":
    unittest.main()
