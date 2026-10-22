"""Offline contracts for the default-off persistent Agent queue."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.agent_api.auth import InternalAgentApiSettings
from backend.agent_api.local_runtime import (
    DisposableQueuedAgentRuntime,
    LocalAgentServiceSettings,
)
from backend.planning.task_queue import (
    PersistentAgentQueueSettings,
    PersistentAgentQueueWorker,
    QueueWorkResult,
    load_persistent_agent_queue_settings,
)
from backend.queued_agent_app import create_queued_agent_app


class PersistentAgentQueueOfflineTests(unittest.TestCase):
    def test_queue_defaults_off_and_invalid_flag_fails(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(load_persistent_agent_queue_settings().enabled)
        with patch.dict(
            os.environ,
            {"PERSISTENT_AGENT_QUEUE_ENABLED": "occasionally"},
            clear=True,
        ):
            with self.assertRaises(ValueError):
                load_persistent_agent_queue_settings()

    def test_disabled_worker_and_runtime_fail_before_database_access(self):
        with self.assertRaisesRegex(ValueError, "persistent_agent_queue_disabled"):
            PersistentAgentQueueWorker(
                None,
                None,
                scope_key="internal-agent:analytics-copilot",
                settings=PersistentAgentQueueSettings(enabled=False),
            )
        runtime = DisposableQueuedAgentRuntime(
            settings=LocalAgentServiceSettings(enabled=True),
            api_settings=InternalAgentApiSettings(
                enabled=True,
                api_key="disabled-queue-test-key-at-least-32-characters",
            ),
            queue_settings=PersistentAgentQueueSettings(enabled=False),
        )
        with self.assertRaisesRegex(ValueError, "persistent_agent_queue_disabled"):
            runtime.start()
        self.assertIsNone(runtime.database_name)

    def test_default_queued_app_does_not_create_database(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "backend.agent_api.auth.load_dotenv"
        ):
            app = create_queued_agent_app()
        runtime = app.state.queued_agent_runtime
        self.assertFalse(runtime.queue_settings.enabled)
        self.assertIsNone(runtime.database_name)

    def test_result_contract_and_worker_id_are_strict(self):
        self.assertEqual(QueueWorkResult(outcome="idle").durable_steps, 0)
        with self.assertRaises(ValueError):
            QueueWorkResult(outcome="completed")
        with self.assertRaises(ValueError):
            QueueWorkResult(outcome="idle", execution_id="12345678-1234-5678-9234-567812345678")
        with self.assertRaises(ValueError):
            PersistentAgentQueueWorker.worker_prefix("INVALID WORKER")


if __name__ == "__main__":
    unittest.main()
