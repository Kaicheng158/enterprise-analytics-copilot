import json
import os
import unittest
from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch

from backend.planning.controlled_runtime import (
    ControlledPlannerSettings,
    build_controlled_planner_runtime,
    load_controlled_planner_settings,
)
from backend.planning.demo import build_quarter_sales_comparison_plan
from backend.planning.executor import BoundedPlanExecutor
from backend.planning.models import PlanningLimits, PlanningRequest
from backend.planning.orchestration import BoundedPlanningOrchestrator
from backend.planning.runtime import PlanningSettings
from backend.planning.structured_provider import (
    PLAN_RESPONSE_SCHEMA_SHA256,
    PLANNER_PROMPT_SHA256,
    PLANNER_PROMPT_VERSION,
    PLANNER_SYSTEM_PROMPT,
    PlannerGenerationResponse,
    PlannerUsage,
    StructuredPlanProvider,
)
from backend.tools.models import ToolError, ToolExecutionContext, ToolExecutionResult


class FakePlannerTransport:
    def __init__(self, content, *, finish_reason="stop", fail=False):
        self.content = content
        self.finish_reason = finish_reason
        self.fail = fail
        self.calls = []

    def generate(self, request):
        self.calls.append(request)
        if self.fail:
            raise RuntimeError("private fake transport detail")
        return PlannerGenerationResponse(
            request_id=f"fake-plan-{len(self.calls)}",
            content=self.content,
            finish_reason=self.finish_reason,
            usage=PlannerUsage(input_tokens=50, output_tokens=100, total_tokens=150),
            latency_ms=1,
        )


class FakeSalesToolExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, call, context):
        self.calls.append((deepcopy(call), context))
        totals = {"2026-Q1": "400.00", "2026-Q2": "600.00"}
        return ToolExecutionResult(
            call_id=f"fake-sales-{len(self.calls)}",
            tool_name=call.name,
            tool_version="1.0.0",
            status="success",
            output={
                "period": call.arguments["period"],
                "region": call.arguments["region"],
                "currency": "USD",
                "order_count": 4,
                "total_sales": totals[call.arguments["period"]],
                "average_order_value": (
                    "100.00" if call.arguments["period"] == "2026-Q1" else "150.00"
                ),
                "data_source": {
                    "kind": "postgresql",
                    "relation": "analytics.sales_orders",
                    "dataset_version": "synthetic-sales-db-v1",
                    "read_only": True,
                },
            },
            error=None,
            latency_ms=0,
        )


class ControlledPlannerFoundationTests(unittest.TestCase):
    def setUp(self):
        self.request = PlanningRequest()
        self.plan = build_quarter_sales_comparison_plan(self.request)
        self.context = ToolExecutionContext(
            principal="analytics-copilot",
            correlation_id="phase5-controlled-planner",
        )

    def run_plan(self, payload, *, limits=None, finish_reason="stop", fail=False):
        content = payload if isinstance(payload, str) else json.dumps(payload)
        transport = FakePlannerTransport(
            content,
            finish_reason=finish_reason,
            fail=fail,
        )
        provider = StructuredPlanProvider(transport)
        tools = FakeSalesToolExecutor()
        orchestrator = BoundedPlanningOrchestrator(
            provider,
            BoundedPlanExecutor(tools, limits=limits),
        )
        return orchestrator.run(self.request, self.context), transport, provider, tools

    def test_fake_transport_generates_structured_plan_then_existing_executor_runs(self):
        result, transport, provider, tools = self.run_plan(
            self.plan.model_dump(mode="json")
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output.growth_rate_percent, Decimal("50.00"))
        self.assertEqual(result.provider_calls, 1)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(len(tools.calls), 2)
        self.assertEqual(result.state.status, "completed")
        self.assertEqual(result.state.budget_units_used, 3)

        generation = transport.calls[0]
        self.assertEqual(generation.prompt_version, PLANNER_PROMPT_VERSION)
        self.assertEqual(generation.prompt_sha256, PLANNER_PROMPT_SHA256)
        self.assertEqual(generation.schema_sha256, PLAN_RESPONSE_SCHEMA_SHA256)
        self.assertEqual(generation.messages[0].content, PLANNER_SYSTEM_PROMPT)
        self.assertEqual(
            json.loads(generation.messages[1].content),
            self.request.model_dump(mode="json"),
        )
        self.assertFalse(
            {"principal", "authorization", "max_budget_units", "tool_result"}
            & set(json.loads(generation.messages[1].content))
        )
        self.assertEqual(generation.config.max_provider_calls, 1)
        self.assertEqual(generation.config.retry_count, 0)

    def test_model_cannot_add_control_fields_or_override_request_scope(self):
        payload = self.plan.model_dump(mode="json")
        payload["max_budget_units"] = 999
        result, _, _, tools = self.run_plan(payload)
        self.assertEqual(result.error.code, "plan_invalid")
        self.assertEqual(tools.calls, [])

        payload = self.plan.model_dump(mode="json")
        payload["region"] = "north"
        for step in payload["steps"]:
            if step["kind"] == "tool":
                step["tool_call"]["arguments"]["region"] = "north"
            else:
                step["computation"]["region"] = "north"
        result, _, _, tools = self.run_plan(payload)
        self.assertEqual(result.error.code, "plan_request_mismatch")
        self.assertEqual(tools.calls, [])

    def test_existing_authorization_and_budget_policy_reject_provider_plan(self):
        unauthorized = self.plan.model_dump(mode="json")
        unauthorized["steps"][0]["tool_call"]["name"] = "get_sales_summary"
        result, _, _, tools = self.run_plan(unauthorized)
        self.assertEqual(result.error.code, "action_not_authorized")
        self.assertEqual(tools.calls, [])

        result, _, _, tools = self.run_plan(
            self.plan.model_dump(mode="json"),
            limits=PlanningLimits(max_budget_units=2),
        )
        self.assertEqual(result.error.code, "budget_limit_exceeded")
        self.assertEqual(tools.calls, [])

    def test_provider_failures_truncation_and_call_limit_fail_closed(self):
        failed, _, _, tools = self.run_plan(
            self.plan.model_dump(mode="json"), fail=True
        )
        self.assertEqual(failed.error.code, "plan_provider_error")
        self.assertNotIn("private", failed.error.message)
        self.assertEqual(tools.calls, [])

        truncated, _, _, tools = self.run_plan(
            self.plan.model_dump(mode="json"), finish_reason="length"
        )
        self.assertEqual(truncated.error.code, "plan_provider_error")
        self.assertEqual(tools.calls, [])

        transport = FakePlannerTransport(self.plan.model_dump_json())
        provider = StructuredPlanProvider(transport)
        self.assertIsInstance(provider.propose(self.request), str)
        with self.assertRaisesRegex(RuntimeError, "planner_provider_call_limit"):
            provider.propose(self.request)
        self.assertEqual(len(transport.calls), 1)

    def test_malformed_response_never_reaches_tool_executor(self):
        result, _, _, tools = self.run_plan("{not-json")
        self.assertEqual(result.error.code, "plan_invalid")
        self.assertEqual(tools.calls, [])

    def test_feature_flags_are_independent_and_default_off(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LLM_PLANNER_ENABLED", None)
            os.environ.pop("MULTI_STEP_PLANNING_ENABLED", None)
            self.assertFalse(load_controlled_planner_settings().enabled)
            with self.assertRaisesRegex(ValueError, "llm_planner_disabled"):
                build_controlled_planner_runtime(
                    FakePlannerTransport(self.plan.model_dump_json())
                )

        with self.assertRaisesRegex(ValueError, "multi_step_planning_disabled"):
            build_controlled_planner_runtime(
                FakePlannerTransport(self.plan.model_dump_json()),
                planner_settings=ControlledPlannerSettings(enabled=True),
                planning_settings=PlanningSettings(enabled=False),
            )

    def test_explicit_fake_composition_builds_without_network_or_database_access(self):
        transport = FakePlannerTransport(self.plan.model_dump_json())
        orchestrator, provider = build_controlled_planner_runtime(
            transport,
            planner_settings=ControlledPlannerSettings(enabled=True),
            planning_settings=PlanningSettings(enabled=True),
            connection_factory=lambda: (_ for _ in ()).throw(
                AssertionError("database must not be opened during composition")
            ),
        )
        self.assertIsInstance(orchestrator, BoundedPlanningOrchestrator)
        self.assertIsInstance(provider, StructuredPlanProvider)
        self.assertEqual(transport.calls, [])

    def test_invalid_feature_flag_and_no_public_route(self):
        with patch.dict(os.environ, {"LLM_PLANNER_ENABLED": "maybe"}):
            with self.assertRaisesRegex(ValueError, "Invalid LLM_PLANNER_ENABLED"):
                load_controlled_planner_settings()

        from backend.main import app as chat_app
        from backend.rag_app import app as rag_app

        paths = {
            route.path
            for app in (chat_app, rag_app)
            for route in app.routes
            if hasattr(route, "path")
        }
        self.assertFalse(any("plan" in path or "agent" in path for path in paths))


if __name__ == "__main__":
    unittest.main()
