import unittest
from decimal import Decimal

from backend.planning.controlled_runtime import (
    ControlledPlannerSettings,
    build_controlled_planner_runtime,
)
from backend.planning.demo import build_quarter_sales_comparison_plan
from backend.planning.models import PlanningRequest
from backend.planning.runtime import PlanningSettings
from backend.planning.structured_provider import (
    PlannerGenerationResponse,
    PlannerUsage,
)
from backend.tools.models import ToolExecutionContext


class FakePlannerTransport:
    def __init__(self, request):
        self.content = build_quarter_sales_comparison_plan(request).model_dump_json()
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        return PlannerGenerationResponse(
            request_id=f"fake-db-plan-{self.calls}",
            content=self.content,
            finish_reason="stop",
            usage=PlannerUsage(input_tokens=50, output_tokens=100, total_tokens=150),
            latency_ms=1,
        )


class ControlledPlannerDatabaseTests(unittest.TestCase):
    def build(self, request):
        transport = FakePlannerTransport(request)
        orchestrator, provider = build_controlled_planner_runtime(
            transport,
            planner_settings=ControlledPlannerSettings(enabled=True),
            planning_settings=PlanningSettings(enabled=True),
        )
        return orchestrator, provider, transport

    def test_fake_planner_runs_through_real_registry_and_postgres_tool(self):
        request = PlanningRequest()
        orchestrator, provider, transport = self.build(request)
        result = orchestrator.run(
            request,
            ToolExecutionContext(
                principal="analytics-copilot",
                correlation_id="phase5-planner-db-happy",
            ),
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output.growth_rate_percent, Decimal("50.00"))
        self.assertEqual(result.state.tool_calls_used, 2)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(transport.calls, 1)

    def test_registry_authorization_remains_independent_of_generated_plan(self):
        request = PlanningRequest()
        orchestrator, provider, transport = self.build(request)
        result = orchestrator.run(
            request,
            ToolExecutionContext(
                principal="untrusted-caller",
                correlation_id="phase5-planner-db-denied",
            ),
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error.code, "tool_execution_failed")
        self.assertEqual(result.state.step_states[0].status, "failed")
        self.assertEqual(result.state.step_states[1].status, "skipped")
        self.assertEqual(provider.calls, 1)
        self.assertEqual(transport.calls, 1)


if __name__ == "__main__":
    unittest.main()
