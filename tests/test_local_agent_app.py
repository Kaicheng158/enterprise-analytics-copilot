"""Offline boundary tests for the default-off localhost Agent service."""

from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend.agent_api.api import create_internal_agent_app
from backend.agent_api.auth import InternalAgentApiSettings
from backend.agent_api.local_runtime import (
    DisposableLocalAgentRuntime,
    LocalAgentServiceSettings,
    load_local_agent_service_settings,
)
from backend.local_agent_app import create_local_agent_app


KEY = "local-agent-test-key-at-least-32-characters"


class AsgiClient:
    def __init__(self, app, *, client_host="127.0.0.1"):
        self.app = app
        self.client_host = client_host

    def post(self, path, *, payload, headers=None):
        async def run():
            output = []
            raw_headers = [(b"content-type", b"application/json")]
            raw_headers.extend(
                (key.lower().encode(), value.encode())
                for key, value in (headers or {}).items()
            )
            body = json.dumps(payload).encode()
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
                    "method": "POST",
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
                json=lambda: json.loads(response_body),
            )

        return asyncio.run(run())


class LocalAgentAppOfflineTests(unittest.TestCase):
    def test_local_service_defaults_off_and_rejects_invalid_flag(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(load_local_agent_service_settings().enabled)
        with patch.dict(
            os.environ,
            {"INTERNAL_AGENT_LOCAL_SERVICE_ENABLED": "sometimes"},
            clear=True,
        ):
            with self.assertRaises(ValueError):
                load_local_agent_service_settings()

    def test_default_factory_does_not_create_a_database(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "backend.agent_api.auth.load_dotenv"
        ):
            app = create_local_agent_app()
        runtime = app.state.local_agent_runtime
        self.assertFalse(runtime.settings.enabled)
        self.assertIsNone(runtime.database_name)

    def test_runtime_refuses_start_when_either_guard_is_off(self):
        runtime = DisposableLocalAgentRuntime(
            settings=LocalAgentServiceSettings(enabled=False),
            api_settings=InternalAgentApiSettings(enabled=False),
        )
        with self.assertRaisesRegex(ValueError, "local_agent_service_disabled"):
            runtime.start()
        self.assertIsNone(runtime.database_name)

        runtime = DisposableLocalAgentRuntime(
            settings=LocalAgentServiceSettings(enabled=True),
            api_settings=InternalAgentApiSettings(enabled=False),
        )
        with self.assertRaisesRegex(ValueError, "internal_agent_api_disabled"):
            runtime.start()
        self.assertIsNone(runtime.database_name)

    def test_loopback_boundary_does_not_trust_forwarding_headers(self):
        app = create_internal_agent_app(
            settings=InternalAgentApiSettings(enabled=True, api_key=KEY),
            require_loopback=True,
        )
        response = AsgiClient(app, client_host="203.0.113.9").post(
            "/internal/agent/tasks",
            payload={
                "task": "compare_quarterly_sales",
                "baseline_period": "2026-Q1",
                "comparison_period": "2026-Q2",
                "region": "all",
            },
            headers={
                "X-Internal-Agent-Key": KEY,
                "Idempotency-Key": "local-test-001",
                "X-Forwarded-For": "127.0.0.1",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "agent_local_only")


if __name__ == "__main__":
    unittest.main()
