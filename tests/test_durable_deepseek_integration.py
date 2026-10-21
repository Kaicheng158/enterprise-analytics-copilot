"""Non-network Phase 5.6 composition and fail-closed contract tests."""

from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
import json
import os
import unittest
from unittest.mock import Mock, patch

from pydantic import SecretStr

from backend.main import app
from backend.planning.deepseek_planner_adapter import (
    DeepSeekPlannerSettings,
    DeepSeekStructuredPlannerTransport,
)
from backend.planning.demo import build_quarter_sales_comparison_plan
from backend.planning.durable_agent_runtime import DurableAgentSettings
from backend.planning.durable_controlled_runtime import (
    DurableControlledPlannerSettings,
    DurablePlannerError,
)
from backend.planning.durable_deepseek_integration import (
    DurableDeepSeekIntegrationSettings,
    build_durable_deepseek_agent_runtime,
    load_durable_deepseek_integration_settings,
)
from backend.planning.models import PlanningRequest
from backend.planning.planner_safety import PlannerCostGuard
from backend.planning.worker_runtime import DurableWorkerSettings


class FakeHttpResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return self._payload[:limit]


def deepseek_payload(content):
    return {
        "id": "phase56-mock-plan",
        "model": "deepseek-flash",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
    }


@contextmanager
def no_database_connection():
    raise AssertionError("database must not be accessed before Plan acceptance")
    yield


class DurableDeepSeekIntegrationContractTests(unittest.TestCase):
    def enabled_settings(self):
        return DurableDeepSeekIntegrationSettings(
            planner=DurableControlledPlannerSettings(
                enabled=True, lease_ttl_seconds=1
            ),
            worker=DurableWorkerSettings(
                enabled=True,
                lease_ttl_seconds=1,
                heartbeat_interval_ms=50,
                operation_timeout_ms=2_000,
            ),
            agent=DurableAgentSettings(enabled=True, max_steps_per_run=8),
        )

    @staticmethod
    def transport(content, *, opener=None, guard=None):
        opener = opener or Mock(return_value=FakeHttpResponse(deepseek_payload(content)))
        return (
            DeepSeekStructuredPlannerTransport(
                DeepSeekPlannerSettings(
                    enabled=True,
                    api_key=SecretStr("phase56-test-key-not-real"),
                ),
                cost_guard=guard or PlannerCostGuard(max_calls=1),
                opener=opener,
            ),
            opener,
        )

    def build(self, content, *, guard=None, opener=None):
        store = Mock()
        store.find_execution_by_idempotency.return_value = None
        transport, opener = self.transport(content, opener=opener, guard=guard)
        agent, provider = build_durable_deepseek_agent_runtime(
            transport,
            store,
            settings=self.enabled_settings(),
            connection_factory=no_database_connection,
        )
        return agent, provider, transport, opener, store

    @staticmethod
    def submit(agent, key):
        return agent.submit(
            PlanningRequest(),
            scope_key="tenant-a:analytics-copilot",
            idempotency_key=key,
            now=datetime(2026, 10, 21, tzinfo=UTC),
        )

    def test_all_existing_feature_boundaries_default_off_and_no_route_is_added(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = load_durable_deepseek_integration_settings()
        self.assertFalse(settings.planner.enabled)
        self.assertFalse(settings.worker.enabled)
        self.assertFalse(settings.agent.enabled)
        with self.assertRaisesRegex(ValueError, "integration_disabled"):
            build_durable_deepseek_agent_runtime(Mock(), Mock())
        paths = {getattr(route, "path", "") for route in app.routes}
        self.assertFalse(any("plan" in path or "agent" in path for path in paths))

    def test_mock_deepseek_unauthorized_plan_stops_before_persistence_or_tools(self):
        payload = build_quarter_sales_comparison_plan(
            PlanningRequest()
        ).model_dump(mode="json")
        payload["steps"][0]["tool_call"]["name"] = "get_sales_summary"
        agent, provider, transport, opener, store = self.build(json.dumps(payload))

        with self.assertRaises(DurablePlannerError) as captured:
            self.submit(agent, "phase56-unauthorized-plan")

        self.assertEqual(captured.exception.code, "action_not_authorized")
        self.assertEqual(provider.calls, 1)
        self.assertEqual(transport.calls, 1)
        self.assertEqual(opener.call_count, 1)
        store.create_or_get_execution.assert_not_called()

    def test_mock_deepseek_over_limit_plan_stops_before_persistence_or_tools(self):
        payload = build_quarter_sales_comparison_plan(
            PlanningRequest()
        ).model_dump(mode="json")
        extra = json.loads(json.dumps(payload["steps"][0]))
        extra["step_id"] = "load_unrequested_sales"
        extra["tool_call"]["arguments"]["period"] = "2026-Q3"
        payload["steps"].insert(2, extra)
        agent, _, _, _, store = self.build(json.dumps(payload))

        with self.assertRaises(DurablePlannerError) as captured:
            self.submit(agent, "phase56-over-limit-plan")

        self.assertEqual(captured.exception.code, "step_limit_exceeded")
        store.create_or_get_execution.assert_not_called()

    def test_provider_cost_guard_stops_before_mock_http_and_persistence(self):
        request = PlanningRequest()
        opener = Mock(side_effect=AssertionError("HTTP must not run"))
        guard = PlannerCostGuard(
            budget_usd=Decimal("0.001"),
            per_call_reserve_usd=Decimal("0.002"),
            max_calls=1,
        )
        agent, provider, transport, _, store = self.build(
            build_quarter_sales_comparison_plan(request).model_dump_json(),
            guard=guard,
            opener=opener,
        )

        with self.assertRaises(DurablePlannerError) as captured:
            self.submit(agent, "phase56-provider-budget-stop")

        self.assertEqual(captured.exception.code, "durable_planner_provider_error")
        self.assertEqual(provider.calls, 1)
        self.assertEqual(transport.calls, 0)
        opener.assert_not_called()
        store.create_or_get_execution.assert_not_called()


if __name__ == "__main__":
    unittest.main()
