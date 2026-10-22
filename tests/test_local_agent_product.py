"""Offline contracts for the default-off local Agent product slice."""

from __future__ import annotations

from decimal import Decimal
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from pydantic import SecretStr

from backend.agent_api.auth import InternalAgentApiSettings
from backend.planning.deepseek_planner_adapter import DeepSeekPlannerSettings
from backend.planning.demo import build_quarter_sales_comparison_plan
from backend.planning.durable_agent_runtime import DurableAgentSettings
from backend.planning.durable_controlled_runtime import DurableControlledPlannerSettings
from backend.planning.durable_deepseek_integration import DurableDeepSeekIntegrationSettings
from backend.planning.models import PlanningRequest
from backend.planning.product_planner import DeepSeekProductPlanProvider
from backend.planning.task_queue import PersistentAgentQueueSettings
from backend.planning.worker_runtime import DurableWorkerSettings
from backend.planning.worker_supervisor import WorkerSupervisorSettings
from backend.product_agent_app import (
    LocalAgentProductSettings,
    create_product_agent_app,
    load_local_agent_product_settings,
)
from tests.db.test_local_agent_http import AsgiClient


class FakeHttpResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, _limit):
        return self._payload


class DynamicPlanOpener:
    def __init__(self):
        self.payloads = []

    def __call__(self, request, timeout):
        payload = json.loads(request.data)
        self.payloads.append(payload)
        planning_request = PlanningRequest.model_validate_json(
            payload["messages"][1]["content"], strict=True
        )
        plan = build_quarter_sales_comparison_plan(planning_request).model_dump_json()
        return FakeHttpResponse(
            {
                "id": f"local-product-{len(self.payloads)}",
                "model": "deepseek-flash",
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": plan},
                }],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                },
            }
        )


class LocalAgentProductOfflineTests(unittest.TestCase):
    def test_product_flag_defaults_off_and_budget_is_validated(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = load_local_agent_product_settings()
        self.assertFalse(settings.enabled)
        with self.assertRaises(ValueError):
            LocalAgentProductSettings(
                enabled=True,
                planner_budget_usd=Decimal("0.001"),
                per_call_reserve_usd=Decimal("0.002"),
            )

    def test_real_adapter_wrapper_supports_multiple_bounded_mock_calls(self):
        opener = DynamicPlanOpener()
        provider = DeepSeekProductPlanProvider(
            DeepSeekPlannerSettings(
                enabled=True,
                api_key=SecretStr("local-product-fake-key-not-real"),
            ),
            max_calls=2,
            opener=opener,
        )
        first = provider.propose(PlanningRequest())
        second = provider.propose(PlanningRequest(region="north"))
        self.assertEqual(json.loads(first)["region"], "all")
        self.assertEqual(json.loads(second)["region"], "north")
        telemetry = provider.telemetry()
        self.assertEqual(telemetry.calls_started, 2)
        self.assertEqual(telemetry.calls_completed, 2)
        self.assertEqual(telemetry.total_tokens, 300)
        self.assertEqual(len(opener.payloads), 2)
        self.assertTrue(all(item["response_format"] == {"type": "json_object"} for item in opener.payloads))

    def test_disabled_app_has_console_but_no_database_or_runtime(self):
        app = create_product_agent_app(
            product_settings=LocalAgentProductSettings(enabled=False),
            api_settings=InternalAgentApiSettings(enabled=False),
            planner_settings=DeepSeekPlannerSettings(enabled=False),
            durable_settings=DurableDeepSeekIntegrationSettings(),
            queue_settings=PersistentAgentQueueSettings(enabled=False),
            supervisor_settings=WorkerSupervisorSettings(enabled=False),
        )
        runtime = app.state.product_agent_runtime
        self.assertFalse(app.state.product_enabled)
        self.assertIsNone(runtime.database_name)
        self.assertNotIn("/rag/chat", {getattr(route, "path", "") for route in app.routes})
        response = AsgiClient(app).get("/agent")
        self.assertEqual(response.status_code, 200)
        remote = AsgiClient(app, client_host="203.0.113.5").get("/agent")
        self.assertEqual(remote.status_code, 403)

    def test_frontend_does_not_persist_or_embed_credentials(self):
        root = Path(__file__).resolve().parents[1] / "frontend" / "agent"
        source = "\n".join(path.read_text() for path in sorted(root.iterdir()))
        self.assertNotIn("localStorage", source)
        self.assertNotIn("sessionStorage", source)
        self.assertNotIn("DEEPSEEK_API_KEY", source)
        self.assertNotIn("INTERNAL_AGENT_API_KEY", source)
        self.assertIn('task: "compare_quarterly_sales"', source)

    def test_product_does_not_start_unless_every_server_guard_is_enabled(self):
        app = create_product_agent_app(
            product_settings=LocalAgentProductSettings(enabled=True),
            api_settings=InternalAgentApiSettings(
                enabled=True,
                api_key="all-guards-test-key-at-least-32-characters",
            ),
            planner_settings=DeepSeekPlannerSettings(
                enabled=True,
                api_key=SecretStr("all-guards-fake-provider-key"),
            ),
            durable_settings=DurableDeepSeekIntegrationSettings(
                planner=DurableControlledPlannerSettings(enabled=True),
                worker=DurableWorkerSettings(enabled=True),
                agent=DurableAgentSettings(enabled=True),
            ),
            queue_settings=PersistentAgentQueueSettings(enabled=True),
            supervisor_settings=WorkerSupervisorSettings(enabled=False),
        )
        self.assertFalse(app.state.product_enabled)
        self.assertIsNone(app.state.product_agent_runtime.database_name)


if __name__ == "__main__":
    unittest.main()
