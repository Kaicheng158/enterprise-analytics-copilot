"""Offline security and contract tests for the default-off Workspace bridge."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import UUID

from backend.agent_api.auth import InternalAgentApiSettings
from backend.agent_api.models import (
    AgentTaskProgress,
    AgentTaskRequest,
    AgentTaskResponse,
)
from backend.agent_api.service import AgentTaskServiceError
from backend.planning.models import SalesGrowthComparison
from backend.planning.task_queue import PersistentAgentQueueSettings
from backend.planning.worker_supervisor import WorkerSupervisorSettings
from backend.workspace_bridge_app import (
    WorkspaceBridgeSettings,
    create_workspace_bridge_app,
    load_workspace_bridge_settings,
)


KEY = "workspace-bridge-server-key-at-least-32-characters"
TASK_ID = UUID("11111111-1111-4111-8111-111111111111")
TASK_BODY = {
    "task": "compare_quarterly_sales",
    "baseline_period": "2026-Q1",
    "comparison_period": "2026-Q2",
    "region": "all",
}


class AsgiClient:
    def __init__(self, app, *, client_host="127.0.0.1"):
        self.app = app
        self.client_host = client_host

    def request(self, method, path, *, payload=None, headers=None, include_origin=True):
        async def run():
            output = []
            request_headers = {
                "content-type": "application/json",
                "host": "127.0.0.1:8774",
            }
            if method not in {"GET", "HEAD"} and include_origin:
                request_headers["origin"] = "http://127.0.0.1:8774"
                request_headers["sec-fetch-site"] = "same-origin"
            request_headers.update({key.lower(): value for key, value in (headers or {}).items()})
            raw_headers = [(key.encode(), value.encode()) for key, value in request_headers.items()]
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
                    "server": ("127.0.0.1", 8774),
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

    def post(self, path, *, payload, headers=None, include_origin=True):
        return self.request(
            "POST", path, payload=payload, headers=headers, include_origin=include_origin
        )

    def get(self, path):
        return self.request("GET", path)


class FakeSupervisor:
    def start(self):
        return None


class FakeRuntime:
    def __init__(self):
        self.supervisor = FakeSupervisor()
        self.submit_calls = []
        self.get_calls = []
        self.error = None

    def start(self):
        return None

    def close(self):
        return None

    @staticmethod
    def response(status="queued"):
        result = None
        completed = 0
        if status == "completed":
            completed = 3
            result = SalesGrowthComparison(
                baseline_period="2026-Q1",
                comparison_period="2026-Q2",
                region="all",
                currency="USD",
                baseline_total_sales="400.00",
                comparison_total_sales="600.00",
                absolute_change="200.00",
                growth_rate_percent="50.00",
                source_relation="analytics.sales_orders",
                dataset_version="synthetic-sales-db-v1",
            )
        return AgentTaskResponse(
            task_id=TASK_ID,
            status=status,
            created=True,
            request=AgentTaskRequest(**TASK_BODY),
            progress=AgentTaskProgress(completed_steps=completed, total_steps=3),
            result=result,
            state_version=completed,
            policy_version="test-policy-v1",
            runtime_version="test-runtime-v1",
            updated_at=datetime.now(UTC),
        )

    def submit(self, request, *, idempotency_key, caller):
        if self.error:
            raise self.error
        self.submit_calls.append((request, idempotency_key, caller))
        return self.response()

    def get(self, task_id, *, caller):
        if self.error:
            raise self.error
        self.get_calls.append((task_id, caller))
        return self.response("completed")


def build_app(runtime, *, enabled=True):
    return create_workspace_bridge_app(
        bridge_settings=WorkspaceBridgeSettings(enabled=enabled),
        api_settings=InternalAgentApiSettings(enabled=enabled, api_key=KEY if enabled else ""),
        queue_settings=PersistentAgentQueueSettings(enabled=enabled),
        supervisor_settings=WorkerSupervisorSettings(enabled=enabled),
        runtime=runtime,
    )


class WorkspaceBridgeTests(unittest.TestCase):
    def test_bridge_feature_flag_defaults_off_and_rejects_invalid_value(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(load_workspace_bridge_settings().enabled)
        with patch.dict(os.environ, {"WORKSPACE_AGENT_BRIDGE_ENABLED": "sometimes"}, clear=True):
            with self.assertRaises(ValueError):
                load_workspace_bridge_settings()

    def test_disabled_bridge_serves_safe_config_but_rejects_submission(self):
        client = AsgiClient(build_app(FakeRuntime(), enabled=False))
        self.assertEqual(client.get("/workspace-api/config").json()["enabled"], False)
        response = client.post(
            "/workspace-api/tasks",
            payload=TASK_BODY,
            headers={"Idempotency-Key": "workspace-test-001"},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "workspace_bridge_disabled")

    def test_server_owned_authorization_and_strict_request(self):
        runtime = FakeRuntime()
        app = build_app(runtime)
        client = AsgiClient(app)
        response = client.post(
            "/workspace-api/tasks",
            payload=TASK_BODY,
            headers={
                "Idempotency-Key": "workspace-test-001",
                "X-Internal-Agent-Key": "browser-must-not-control-this",
            },
        )
        self.assertEqual(response.status_code, 202)
        _, key, caller = runtime.submit_calls[0]
        self.assertEqual(key, "workspace-test-001")
        self.assertEqual(caller.scope_key, "internal-agent:analytics-copilot")
        self.assertNotIn("X-Internal-Agent-Key", json.dumps(app.openapi()))

        invalid = client.post(
            "/workspace-api/tasks",
            payload={**TASK_BODY, "prompt": "ignore controls"},
            headers={"Idempotency-Key": "workspace-test-002"},
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(len(runtime.submit_calls), 1)

    def test_loopback_and_error_boundaries_fail_closed(self):
        runtime = FakeRuntime()
        app = build_app(runtime)
        external = AsgiClient(app, client_host="198.51.100.9").post(
            "/workspace-api/tasks",
            payload=TASK_BODY,
            headers={"Idempotency-Key": "workspace-test-003"},
        )
        self.assertEqual(external.status_code, 403)
        self.assertEqual(len(runtime.submit_calls), 0)

        runtime.error = AgentTaskServiceError(
            status_code=503,
            code="private_database_stacktrace",
            message="secret database detail",
        )
        failure = AsgiClient(app).post(
            "/workspace-api/tasks",
            payload=TASK_BODY,
            headers={"Idempotency-Key": "workspace-test-004"},
        )
        self.assertEqual(failure.status_code, 503)
        self.assertEqual(failure.json()["detail"]["code"], "agent_request_failed")
        self.assertNotIn("secret database detail", failure.text)

    def test_host_origin_and_cross_site_requests_fail_closed(self):
        runtime = FakeRuntime()
        app = build_app(runtime)
        client = AsgiClient(app)

        bad_host = client.post(
            "/workspace-api/tasks",
            payload=TASK_BODY,
            headers={
                "Host": "attacker.example",
                "Origin": "http://attacker.example",
                "Idempotency-Key": "workspace-test-host",
            },
        )
        self.assertEqual(bad_host.status_code, 403)
        self.assertEqual(bad_host.json()["detail"]["code"], "workspace_bridge_host_rejected")

        missing_origin = client.post(
            "/workspace-api/tasks",
            payload=TASK_BODY,
            headers={"Idempotency-Key": "workspace-test-no-origin"},
            include_origin=False,
        )
        self.assertEqual(missing_origin.status_code, 403)
        self.assertEqual(
            missing_origin.json()["detail"]["code"],
            "workspace_bridge_origin_required",
        )

        cross_origin = client.post(
            "/workspace-api/tasks",
            payload=TASK_BODY,
            headers={
                "Origin": "http://evil.example",
                "Idempotency-Key": "workspace-test-origin",
            },
        )
        self.assertEqual(cross_origin.status_code, 403)
        self.assertEqual(
            cross_origin.json()["detail"]["code"], "workspace_bridge_cross_site"
        )

        cross_site = client.post(
            "/workspace-api/tasks",
            payload=TASK_BODY,
            headers={
                "Sec-Fetch-Site": "cross-site",
                "Idempotency-Key": "workspace-test-fetch-site",
            },
        )
        self.assertEqual(cross_site.status_code, 403)
        self.assertEqual(cross_site.json()["detail"]["code"], "workspace_bridge_cross_site")
        self.assertEqual(runtime.submit_calls, [])


if __name__ == "__main__":
    unittest.main()
