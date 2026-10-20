import os
import unittest
from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch

from pydantic import ValidationError

from backend.planning import (
    BoundedPlanExecutor,
    BoundedPlanningOrchestrator,
    PlanValidator,
    PlanningLimits,
    PlanningRequest,
    PlanningSettings,
    build_planning_runtime,
    build_quarter_sales_comparison_plan,
    load_planning_settings,
)
from backend.planning.models import AgentState, Plan
from backend.tools.models import (
    ToolError,
    ToolExecutionContext,
    ToolExecutionResult,
)


def sales_output(period, *, region="all", total="400.00", dataset="synthetic-sales-db-v1"):
    return {
        "period": period,
        "region": region,
        "currency": "USD",
        "order_count": 4,
        "total_sales": total,
        "average_order_value": "100.00",
        "data_source": {
            "kind": "postgresql",
            "relation": "analytics.sales_orders",
            "dataset_version": dataset,
            "read_only": True,
        },
    }


class FakePlanProvider:
    def __init__(self, plan=None, raw=None):
        self.plan = plan
        self.raw = raw
        self.calls = 0

    def propose(self, request):
        self.calls += 1
        if self.raw is RuntimeError:
            raise RuntimeError("private provider detail")
        return self.raw if self.raw is not None else self.plan.model_dump_json()


class FakeToolExecutor:
    def __init__(
        self,
        *,
        outputs=None,
        fail_at=None,
        raise_at=None,
        invalid_result_at=None,
        clock=None,
        advance=0,
    ):
        self.outputs = outputs or {
            "2026-Q1": sales_output("2026-Q1", total="400.00"),
            "2026-Q2": sales_output("2026-Q2", total="600.00"),
        }
        self.fail_at = fail_at
        self.raise_at = raise_at
        self.invalid_result_at = invalid_result_at
        self.clock = clock
        self.advance = advance
        self.calls = []

    def execute(self, call, context):
        self.calls.append((deepcopy(call), context))
        if self.clock and self.advance:
            self.clock.advance(self.advance)
        index = len(self.calls)
        if self.raise_at == index:
            raise RuntimeError("private tool failure")
        if self.invalid_result_at == index:
            return {"private": "malformed result"}
        if self.fail_at == index:
            return ToolExecutionResult(
                call_id=f"fake-{index}",
                tool_name=call.name,
                tool_version="1.0.0",
                status="error",
                output=None,
                error=ToolError(
                    code="tool_not_authorized", message="Tool is not authorized"
                ),
                latency_ms=0,
            )
        return ToolExecutionResult(
            call_id=f"fake-{index}",
            tool_name=call.name,
            tool_version="1.0.0",
            status="success",
            output=deepcopy(self.outputs[call.arguments["period"]]),
            error=None,
            latency_ms=0,
        )


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class BoundedPlanningTests(unittest.TestCase):
    def setUp(self):
        self.request = PlanningRequest()
        self.plan = build_quarter_sales_comparison_plan(self.request)
        self.context = ToolExecutionContext(
            principal="analytics-copilot", correlation_id="phase5-test"
        )

    def orchestrator(self, tool_executor=None, *, plan=None, limits=None, clock=None):
        provider = FakePlanProvider(plan=plan or self.plan)
        executor = BoundedPlanExecutor(
            tool_executor or FakeToolExecutor(),
            limits=limits,
            clock=clock or __import__("time").monotonic,
        )
        return BoundedPlanningOrchestrator(provider, executor), provider

    def test_fake_provider_executes_two_tools_then_python_growth(self):
        tool_executor = FakeToolExecutor()
        orchestrator, provider = self.orchestrator(tool_executor)
        result = orchestrator.run(self.request, self.context)

        self.assertEqual(result.status, "completed")
        self.assertEqual(provider.calls, 1)
        self.assertEqual(len(tool_executor.calls), 2)
        self.assertEqual(result.output.baseline_total_sales, Decimal("400.00"))
        self.assertEqual(result.output.comparison_total_sales, Decimal("600.00"))
        self.assertEqual(result.output.absolute_change, Decimal("200.00"))
        self.assertEqual(result.output.growth_rate_percent, Decimal("50.00"))
        self.assertEqual(result.state.tool_calls_used, 2)
        self.assertEqual(result.state.budget_units_used, 3)
        self.assertEqual(result.state.completed_step_ids, (
            "load_baseline_sales", "load_comparison_sales", "calculate_growth"
        ))
        self.assertGreaterEqual(len(result.state_history), 7)

    def test_contracts_reject_extra_fields_and_validator_rejects_forward_dependencies(self):
        payload = self.plan.model_dump(mode="python")
        payload["unexpected"] = True
        with self.assertRaises(ValidationError):
            Plan.model_validate(payload, strict=True)

        payload = self.plan.model_dump(mode="python")
        first = payload["steps"][0]
        payload["steps"] = (
            {**first, "depends_on": ("load_comparison_sales",)},
            *payload["steps"][1:],
        )
        plan = Plan.model_validate(payload, strict=True)
        validation = PlanValidator().validate(plan)
        self.assertEqual(validation.status, "invalid")
        self.assertEqual(validation.error.code, "plan_dependency_order_invalid")

    def test_invalid_provider_json_and_request_mismatch_fail_before_tools(self):
        tool_executor = FakeToolExecutor()
        invalid = BoundedPlanningOrchestrator(
            FakePlanProvider(raw="not-json"), BoundedPlanExecutor(tool_executor)
        ).run(self.request, self.context)
        self.assertEqual(invalid.error.code, "plan_invalid")
        self.assertEqual(tool_executor.calls, [])

        other_request = PlanningRequest(region="north")
        mismatch, provider = self.orchestrator(tool_executor)
        result = mismatch.run(other_request, self.context)
        self.assertEqual(result.error.code, "plan_request_mismatch")
        self.assertEqual(provider.calls, 1)
        self.assertEqual(tool_executor.calls, [])

        provider_failure = BoundedPlanningOrchestrator(
            FakePlanProvider(raw=RuntimeError), BoundedPlanExecutor(tool_executor)
        ).run(self.request, self.context)
        self.assertEqual(provider_failure.error.code, "plan_provider_error")
        self.assertNotIn("private provider detail", provider_failure.error.message)
        self.assertEqual(tool_executor.calls, [])

    def test_step_tool_and_budget_limits_fail_before_execution(self):
        cases = (
            (PlanningLimits(max_steps=2), "step_limit_exceeded"),
            (PlanningLimits(max_tool_calls=1), "tool_call_limit_exceeded"),
            (PlanningLimits(max_budget_units=2), "budget_limit_exceeded"),
        )
        for limits, code in cases:
            with self.subTest(code=code):
                tool_executor = FakeToolExecutor()
                orchestrator, _ = self.orchestrator(tool_executor, limits=limits)
                result = orchestrator.run(self.request, self.context)
                self.assertEqual(result.error.code, code)
                self.assertEqual(tool_executor.calls, [])
                self.assertTrue(all(
                    state.status == "skipped" for state in result.state.step_states
                ))

    def test_unapproved_tool_is_rejected_before_executor(self):
        payload = self.plan.model_dump(mode="python")
        first = payload["steps"][0]
        first["tool_call"]["name"] = "get_sales_summary"
        plan = Plan.model_validate(payload, strict=True)
        tool_executor = FakeToolExecutor()
        orchestrator, _ = self.orchestrator(tool_executor, plan=plan)
        result = orchestrator.run(self.request, self.context)
        self.assertEqual(result.error.code, "action_not_authorized")
        self.assertEqual(tool_executor.calls, [])

    def test_invalid_dependency_graph_is_rejected_before_tools(self):
        payload = self.plan.model_dump(mode="python")
        payload["steps"][0]["depends_on"] = ("load_comparison_sales",)
        payload["steps"][1]["depends_on"] = ("load_baseline_sales",)
        cyclic = Plan.model_validate(payload, strict=True)
        tool_executor = FakeToolExecutor()
        orchestrator, _ = self.orchestrator(tool_executor, plan=cyclic)
        result = orchestrator.run(self.request, self.context)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error.code, "plan_cycle_detected")
        self.assertEqual(tool_executor.calls, [])
        self.assertTrue(
            all(step.status == "skipped" for step in result.state.step_states)
        )

    def test_tool_failure_stops_and_skips_remaining_steps(self):
        tool_executor = FakeToolExecutor(fail_at=1)
        orchestrator, _ = self.orchestrator(tool_executor)
        result = orchestrator.run(self.request, self.context)
        self.assertEqual(result.error.code, "tool_execution_failed")
        self.assertEqual(len(tool_executor.calls), 1)
        statuses = [state.status for state in result.state.step_states]
        self.assertEqual(statuses, ["failed", "skipped", "skipped"])

    def test_unexpected_or_malformed_tool_result_stops_safely(self):
        for tool_executor in (
            FakeToolExecutor(raise_at=1),
            FakeToolExecutor(invalid_result_at=1),
        ):
            with self.subTest(executor=tool_executor):
                orchestrator, _ = self.orchestrator(tool_executor)
                result = orchestrator.run(self.request, self.context)
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.error.code, "tool_execution_failed")
                self.assertNotIn("private", result.error.message)
                self.assertEqual(len(tool_executor.calls), 1)
                self.assertEqual(
                    [state.status for state in result.state.step_states],
                    ["failed", "skipped", "skipped"],
                )

    def test_unexpected_compute_exception_is_sanitized(self):
        tool_executor = FakeToolExecutor()
        orchestrator, _ = self.orchestrator(tool_executor)
        with patch.object(
            BoundedPlanExecutor,
            "_compute_growth",
            side_effect=RuntimeError("private compute detail"),
        ):
            result = orchestrator.run(self.request, self.context)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error.code, "state_consistency_error")
        self.assertNotIn("private", result.error.message)
        self.assertEqual(len(tool_executor.calls), 2)
        self.assertEqual(result.state.step_states[-1].status, "failed")

    def test_timeout_stops_after_current_tool_and_before_next_call(self):
        clock = FakeClock()
        tool_executor = FakeToolExecutor(clock=clock, advance=0.020)
        orchestrator, _ = self.orchestrator(
            tool_executor,
            limits=PlanningLimits(timeout_ms=10),
            clock=clock,
        )
        result = orchestrator.run(self.request, self.context)
        self.assertEqual(result.error.code, "plan_timeout")
        self.assertEqual(len(tool_executor.calls), 1)

        boundary_clock = FakeClock()
        boundary_executor = FakeToolExecutor(
            clock=boundary_clock, advance=0.010
        )
        orchestrator, _ = self.orchestrator(
            boundary_executor,
            limits=PlanningLimits(timeout_ms=10),
            clock=boundary_clock,
        )
        boundary = orchestrator.run(self.request, self.context)
        self.assertEqual(boundary.error.code, "plan_timeout")
        self.assertEqual(len(boundary_executor.calls), 1)

    def test_scope_mismatch_and_zero_baseline_fail_safely(self):
        mismatched = {
            "2026-Q1": sales_output("2026-Q1", total="400.00"),
            "2026-Q2": sales_output("2026-Q2", region="south", total="600.00"),
        }
        orchestrator, _ = self.orchestrator(FakeToolExecutor(outputs=mismatched))
        result = orchestrator.run(self.request, self.context)
        self.assertEqual(result.error.code, "comparison_scope_mismatch")

        zero = {
            "2026-Q1": sales_output("2026-Q1", total="0.00"),
            "2026-Q2": sales_output("2026-Q2", total="100.00"),
        }
        orchestrator, _ = self.orchestrator(FakeToolExecutor(outputs=zero))
        result = orchestrator.run(self.request, self.context)
        self.assertEqual(result.error.code, "growth_rate_undefined")

    def test_invalid_intermediate_money_and_aov_semantics_fail_safely(self):
        invalid_money = {
            "2026-Q1": sales_output("2026-Q1", total="NaN"),
            "2026-Q2": sales_output("2026-Q2", total="600.00"),
        }
        orchestrator, _ = self.orchestrator(FakeToolExecutor(outputs=invalid_money))
        result = orchestrator.run(self.request, self.context)
        self.assertEqual(result.error.code, "intermediate_output_invalid")

        excessive_money = {
            "2026-Q1": sales_output(
                "2026-Q1", total=f"{'9' * 40}.00"
            ),
            "2026-Q2": sales_output("2026-Q2", total="600.00"),
        }
        orchestrator, _ = self.orchestrator(
            FakeToolExecutor(outputs=excessive_money)
        )
        result = orchestrator.run(self.request, self.context)
        self.assertEqual(result.error.code, "intermediate_output_invalid")

        invalid_aov = {
            "2026-Q1": {
                **sales_output("2026-Q1", total="0.00"),
                "order_count": 0,
                "average_order_value": "0.00",
            },
            "2026-Q2": sales_output("2026-Q2", total="600.00"),
        }
        orchestrator, _ = self.orchestrator(FakeToolExecutor(outputs=invalid_aov))
        result = orchestrator.run(self.request, self.context)
        self.assertEqual(result.error.code, "intermediate_output_invalid")

    def test_state_contract_rejects_inconsistent_completed_accounting(self):
        with self.assertRaises(ValidationError):
            AgentState(
                plan_id="bad",
                status="completed",
                current_step_id=None,
                step_states=(),
                completed_step_ids=("missing",),
                steps_executed=1,
                tool_calls_used=0,
                budget_units_used=0,
                elapsed_ms=0,
                error=None,
            )

    def test_runtime_is_default_off_and_no_public_planning_route_exists(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MULTI_STEP_PLANNING_ENABLED", None)
            self.assertFalse(load_planning_settings().enabled)
            with self.assertRaisesRegex(ValueError, "multi_step_planning_disabled"):
                build_planning_runtime(FakePlanProvider(plan=self.plan))

        from backend.main import app as chat_app
        from backend.rag_app import app as rag_app

        routes = [
            route.path
            for app in (chat_app, rag_app)
            for route in app.routes
            if hasattr(route, "path")
        ]
        self.assertFalse(any("plan" in route or "agent" in route for route in routes))

    def test_invalid_feature_flag_fails_closed(self):
        with patch.dict(os.environ, {"MULTI_STEP_PLANNING_ENABLED": "maybe"}):
            with self.assertRaisesRegex(ValueError, "Invalid MULTI_STEP"):
                load_planning_settings()


if __name__ == "__main__":
    unittest.main()
