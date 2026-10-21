import os
import unittest
from unittest.mock import Mock, patch

from backend.main import app
from backend.planning.worker_runtime import (
    DurableWorkerRuntime,
    DurableWorkerSettings,
    load_durable_worker_settings,
)


class DurableWorkerRuntimeContractTests(unittest.TestCase):
    def test_feature_flag_defaults_off_and_no_public_route_exists(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(load_durable_worker_settings().enabled)
        paths = {getattr(route, "path", "") for route in app.routes}
        self.assertFalse(any("worker" in path for path in paths))

    def test_disabled_runtime_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "durable_worker_runtime_disabled"):
            DurableWorkerRuntime(Mock(), settings=DurableWorkerSettings(enabled=False))

    def test_heartbeat_must_be_faster_than_lease_expiry(self):
        with self.assertRaisesRegex(ValueError, "shorter than lease TTL"):
            DurableWorkerSettings(
                enabled=True,
                lease_ttl_seconds=1,
                heartbeat_interval_ms=1_000,
            )

    def test_invalid_feature_flag_is_rejected(self):
        with patch.dict(os.environ, {"DURABLE_WORKER_RUNTIME_ENABLED": "sometimes"}):
            with self.assertRaisesRegex(ValueError, "Invalid DURABLE_WORKER"):
                load_durable_worker_settings()


if __name__ == "__main__":
    unittest.main()
