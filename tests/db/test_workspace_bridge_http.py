"""Phase 5.8b bridge integration against an isolated PostgreSQL database."""

from __future__ import annotations

import asyncio
import json
from time import monotonic, sleep
from types import SimpleNamespace
import unittest

from backend.agent_api.auth import InternalAgentApiSettings
from backend.agent_api.local_runtime import (
    DisposableManagedAgentRuntime,
    LocalAgentServiceSettings,
)
from backend.planning.task_queue import PersistentAgentQueueSettings
from backend.planning.worker_supervisor import WorkerSupervisorSettings
from backend.workspace_bridge_app import WorkspaceBridgeSettings, create_workspace_bridge_app


KEY = "workspace-bridge-db-key-at-least-32-characters"
TASK_BODY = {
    "task": "compare_quarterly_sales",
    "baseline_period": "2026-Q1",
    "comparison_period": "2026-Q2",
    "region": "all",
}


class AsgiClient:
    def __init__(self, app):
        self.app = app

    def request(self, method, path, *, payload=None, headers=None):
        async def run():
            output = []
            request_headers = {"content-type":"application/json", "host":"127.0.0.1:8774"}
            if method not in {"GET", "HEAD"}:
                request_headers.update({"origin":"http://127.0.0.1:8774", "sec-fetch-site":"same-origin"})
            request_headers.update({key.lower():value for key,value in (headers or {}).items()})
            raw_headers = [(key.encode(),value.encode()) for key,value in request_headers.items()]
            body = b"" if payload is None else json.dumps(payload).encode()
            sent = False

            async def receive():
                nonlocal sent
                if sent:
                    return {"type": "http.disconnect"}
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}

            async def send(message):
                output.append(message)

            await self.app(
                {"type":"http","asgi":{"version":"3.0"},"http_version":"1.1","method":method,
                 "scheme":"http","path":path,"raw_path":path.encode(),"query_string":b"",
                 "headers":raw_headers,"server":("127.0.0.1",8774),"client":("127.0.0.1",12345)},
                receive, send,
            )
            raw = b"".join(item.get("body", b"") for item in output)
            return SimpleNamespace(status_code=output[0]["status"], text=raw.decode(), json=lambda:json.loads(raw))
        return asyncio.run(run())

    def post(self, path, *, payload, headers):
        return self.request("POST", path, payload=payload, headers=headers)

    def get(self, path):
        return self.request("GET", path)


class WorkspaceBridgeDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = InternalAgentApiSettings(enabled=True, api_key=KEY)
        cls.queue = PersistentAgentQueueSettings(enabled=True, poll_interval_ms=25)
        cls.supervisor_settings = WorkerSupervisorSettings(
            enabled=True, max_workers=2, poll_interval_ms=25, shutdown_grace_seconds=5
        )
        cls.runtime = DisposableManagedAgentRuntime(
            settings=LocalAgentServiceSettings(enabled=True, max_active_executions=20),
            api_settings=cls.api,
            queue_settings=cls.queue,
            supervisor_settings=cls.supervisor_settings,
        )
        cls.runtime.start()
        cls.runtime.supervisor.start()
        cls.addClassCleanup(cls.runtime.close)
        cls.app = create_workspace_bridge_app(
            bridge_settings=WorkspaceBridgeSettings(enabled=True),
            api_settings=cls.api,
            queue_settings=cls.queue,
            supervisor_settings=cls.supervisor_settings,
            runtime=cls.runtime,
        )
        cls.client = AsgiClient(cls.app)

    def setUp(self):
        with self.runtime.connection_factory() as database:
            database.execute(
                "TRUNCATE agent_runtime.reconciliation_events,agent_runtime.step_receipts,"
                "agent_runtime.checkpoints,agent_runtime.executions CASCADE"
            )
            database.commit()
        self.runtime.provider.calls = 0

    def wait_terminal(self, task_id):
        deadline = monotonic() + 8
        seen = []
        while monotonic() < deadline:
            response = self.client.get(f"/workspace-api/tasks/{task_id}")
            if response.status_code == 409 and response.json().get("detail", {}).get("code") == "agent_status_changed":
                sleep(0.01)
                continue
            self.assertEqual(response.status_code, 200)
            body = response.json(); seen.append(body["status"])
            if body["status"] in {"completed", "failed", "needs_reconciliation"}:
                return body, seen
            sleep(0.03)
        self.fail("workspace task did not reach a terminal state")

    def test_submit_poll_result_and_duplicate_recovery_use_existing_runtime(self):
        headers = {"Idempotency-Key": "workspace-db-integration-001"}
        first = self.client.post("/workspace-api/tasks", payload=TASK_BODY, headers=headers)
        self.assertEqual(first.status_code, 202)
        duplicate = self.client.post("/workspace-api/tasks", payload=TASK_BODY, headers=headers)
        self.assertEqual(duplicate.status_code, 202)
        self.assertEqual(first.json()["task_id"], duplicate.json()["task_id"])

        completed, seen = self.wait_terminal(first.json()["task_id"])
        self.assertIn(first.json()["status"], {"queued", "running", "completed"})
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["progress"], {"completed_steps": 3, "total_steps": 3})
        self.assertEqual(completed["result"]["baseline_total_sales"], "400.00")
        self.assertEqual(completed["result"]["comparison_total_sales"], "600.00")
        self.assertEqual(completed["result"]["growth_rate_percent"], "50.00")
        self.assertEqual(completed["result"]["source_relation"], "analytics.sales_orders")
        self.assertEqual(completed["result"]["dataset_version"], "synthetic-sales-db-v1")
        self.assertEqual(self.runtime.provider.calls, 1)
        self.assertEqual(len(self.runtime.store.list_receipts(first.json()["task_id"])), 3)
        self.assertTrue(seen)

    def test_same_key_different_request_conflicts_without_second_plan(self):
        headers = {"Idempotency-Key": "workspace-db-integration-002"}
        self.assertEqual(self.client.post("/workspace-api/tasks", payload=TASK_BODY, headers=headers).status_code, 202)
        conflict = self.client.post(
            "/workspace-api/tasks", payload={**TASK_BODY, "region": "north"}, headers=headers
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["detail"]["code"], "agent_idempotency_conflict")


if __name__ == "__main__":
    unittest.main()
