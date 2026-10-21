import os
import unittest
from unittest.mock import Mock, patch

from backend.main import app
from backend.planning.durable_agent_runtime import (
    DurableAgentRuntime,
    DurableAgentSettings,
    load_durable_agent_settings,
)


class DurableAgentRuntimeContractTests(unittest.TestCase):
    def test_feature_flag_defaults_off_and_no_public_route_exists(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(load_durable_agent_settings().enabled)
        paths = {getattr(route, "path", "") for route in app.routes}
        self.assertFalse(any("agent" in path for path in paths))

    def test_disabled_runtime_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "durable_agent_runtime_disabled"):
            DurableAgentRuntime(
                Mock(), Mock(), settings=DurableAgentSettings(enabled=False)
            )

    def test_invalid_flag_and_worker_prefix_are_rejected(self):
        with patch.dict(os.environ, {"DURABLE_AGENT_RUNTIME_ENABLED": "maybe"}):
            with self.assertRaisesRegex(ValueError, "Invalid DURABLE_AGENT"):
                load_durable_agent_settings()
        runtime = DurableAgentRuntime(
            Mock(), Mock(), settings=DurableAgentSettings(enabled=True)
        )
        with self.assertRaisesRegex(ValueError, "invalid worker ID prefix"):
            runtime.run_to_terminal(Mock(), worker_id_prefix="Invalid Worker")


if __name__ == "__main__":
    unittest.main()
