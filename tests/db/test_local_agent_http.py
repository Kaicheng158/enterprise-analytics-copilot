"""Protected localhost HTTP integration tests in a disposable PostgreSQL database."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from threading import Lock, Thread
from types import SimpleNamespace
import unittest

import psycopg

from backend.agent_api.api import create_internal_agent_app
from backend.agent_api.auth import InternalAgentApiSettings
from backend.agent_api.local_runtime import (
    DeterministicLocalPlanProvider,
    DisposableLocalAgentRuntime,
    LocalAgentServiceSettings,
)
from backend.planning.models import PlanningRequest


KEY = "local-http-integration-key-at-least-32-characters"
TASK_BODY = {
    "task": "compare_quarterly_sales",
    "baseline_period": "2026-Q1",
    "comparison_period": "2026-Q2",
    "region": "all",
}


class TogglePlanProvider(DeterministicLocalPlanProvider):
    def __init__(self):
        super().__init__()
        self.fail = False

    def propose(self, request):
        if self.fail:
            raise RuntimeError("synthetic planner failure with private detail")
        return super().propose(request)


class AsgiClient:
    def __init__(self, app, *, client_host="127.0.0.1"):
        self.app = app
        self.client_host = client_host

    def request(self, method, path, *, payload=None, headers=None):
        async def run():
            output = []
            raw_headers = [(b"content-type", b"application/json")]
            raw_headers.extend(
                (key.lower().encode(), value.encode())
                for key, value in (headers or {}).items()
            )
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
                {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "1.1",
                    "method": method,
                    "scheme": "http",
                    "path": path,
                    "raw_path": path.encode(),
                    "query_string": b"",
                    "headers": raw_headers,
                    "server": ("127.0.0.1", 8771),
                    "client": (self.client_host, 12345),
                },
                receive,
                send,
            )
            response_body = b"".join(item.get("body", b"") for item in output)
            return SimpleNamespace(
                status_code=output[0]["status"],
                text=response_body.decode(),
                json=lambda: json.loads(response_body),
            )

        return asyncio.run(run())

    def post(self, path, *, payload, headers=None):
        return self.request("POST", path, payload=payload, headers=headers)

    def get(self, path, *, headers=None):
        return self.request("GET", path, headers=headers)


class LocalAgentHttpDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.provider = TogglePlanProvider()
        cls.api_settings = InternalAgentApiSettings(enabled=True, api_key=KEY)
        cls.runtime = DisposableLocalAgentRuntime(
            settings=LocalAgentServiceSettings(enabled=True),
            api_settings=cls.api_settings,
            provider=cls.provider,
        )
        cls.runtime.start()
        cls.addClassCleanup(cls.runtime.close)
        cls.app = create_internal_agent_app(
            settings=cls.api_settings,
            controller=cls.runtime,
            require_loopback=True,
        )

    def setUp(self):
        with self.runtime.connection_factory() as database:
            database.execute(
                "TRUNCATE agent_runtime.reconciliation_events,"
                "agent_runtime.step_receipts,agent_runtime.checkpoints,"
                "agent_runtime.executions CASCADE"
            )
            database.commit()
        self.provider.calls = 0
        self.provider.fail = False
        self.client = AsgiClient(self.app)
        self.headers = {
            "X-Internal-Agent-Key": KEY,
            "Idempotency-Key": "local-http-request-001",
        }

    def test_submit_status_and_result_use_existing_durable_runtime(self):
        submitted = self.client.post(
            "/internal/agent/tasks", payload=TASK_BODY, headers=self.headers
        )
        self.assertEqual(submitted.status_code, 202)
        body = submitted.json()
        self.assertTrue(body["created"])
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["progress"], {"completed_steps": 3, "total_steps": 3})
        self.assertEqual(body["result"]["baseline_total_sales"], "400.00")
        self.assertEqual(body["result"]["comparison_total_sales"], "600.00")
        self.assertEqual(body["result"]["growth_rate_percent"], "50.00")

        status = self.client.get(
            f"/internal/agent/tasks/{body['task_id']}",
            headers={"X-Internal-Agent-Key": KEY},
        )
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["result"], body["result"])
        self.assertEqual(self.provider.calls, 1)
        receipts = self.runtime.store.list_receipts(body["task_id"])
        self.assertEqual(len(receipts), 3)
        self.assertTrue(all(receipt.status == "succeeded" for receipt in receipts))

    def test_authentication_and_loopback_fail_closed(self):
        missing = self.client.post(
            "/internal/agent/tasks",
            payload=TASK_BODY,
            headers={"Idempotency-Key": "local-http-request-002"},
        )
        self.assertEqual(missing.status_code, 401)
        external = AsgiClient(self.app, client_host="198.51.100.3").post(
            "/internal/agent/tasks", payload=TASK_BODY, headers=self.headers
        )
        self.assertEqual(external.status_code, 403)
        self.assertEqual(self.provider.calls, 0)

    def test_cross_scope_task_is_hidden(self):
        creation = self.runtime.agent.submit(
            PlanningRequest(),
            scope_key="another-internal-scope",
            idempotency_key="cross-scope-hidden-001",
            now=datetime.now(UTC),
        )
        response = self.client.get(
            f"/internal/agent/tasks/{creation.execution.execution_id}",
            headers={"X-Internal-Agent-Key": KEY},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "agent_task_not_found")

    def test_concurrent_duplicate_is_single_flight_and_request_conflict_is_409(self):
        responses = []
        response_lock = Lock()

        def submit():
            response = AsgiClient(self.app).post(
                "/internal/agent/tasks",
                payload=TASK_BODY,
                headers=self.headers,
            )
            with response_lock:
                responses.append(response)

        first = Thread(target=submit)
        second = Thread(target=submit)
        first.start()
        second.start()
        first.join(5)
        second.join(5)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual([item.status_code for item in responses], [202, 202])
        bodies = [item.json() for item in responses]
        self.assertEqual({item["created"] for item in bodies}, {True, False})
        self.assertEqual(len({item["task_id"] for item in bodies}), 1)
        self.assertTrue(all(item["status"] == "completed" for item in bodies))
        self.assertEqual(self.provider.calls, 1)
        task_id = bodies[0]["task_id"]
        self.assertEqual(len(self.runtime.store.list_receipts(task_id)), 3)

        conflict_body = {**TASK_BODY, "region": "north"}
        conflict = self.client.post(
            "/internal/agent/tasks",
            payload=conflict_body,
            headers=self.headers,
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(
            conflict.json()["detail"]["code"], "agent_idempotency_conflict"
        )

    def test_provider_failure_returns_safe_error_without_execution(self):
        self.provider.fail = True
        response = self.client.post(
            "/internal/agent/tasks", payload=TASK_BODY, headers=self.headers
        )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"]["code"], "agent_plan_rejected")
        self.assertNotIn("private detail", response.text)
        with self.runtime.connection_factory() as database:
            count = database.execute(
                "SELECT count(*) FROM agent_runtime.executions"
            ).fetchone()[0]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
