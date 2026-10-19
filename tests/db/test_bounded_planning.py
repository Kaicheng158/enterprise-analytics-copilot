import unittest
from decimal import Decimal

from backend.planning import (
    PlanningRequest,
    PlanningSettings,
    build_planning_runtime,
    build_quarter_sales_comparison_plan,
)
from backend.tools.models import ToolExecutionContext


class FakePlanProvider:
    def __init__(self):
        self.calls = 0

    def propose(self, request):
        self.calls += 1
        return build_quarter_sales_comparison_plan(request).model_dump_json()


class BoundedPlanningDatabaseTests(unittest.TestCase):
    def test_real_postgres_q1_q2_comparison(self):
        provider = FakePlanProvider()
        runtime = build_planning_runtime(
            provider, settings=PlanningSettings(enabled=True)
        )
        result = runtime.run(
            PlanningRequest(),
            ToolExecutionContext(
                principal="analytics-copilot",
                correlation_id="phase5-db-happy",
            ),
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(provider.calls, 1)
        self.assertEqual(result.output.baseline_total_sales, Decimal("400.00"))
        self.assertEqual(result.output.comparison_total_sales, Decimal("600.00"))
        self.assertEqual(result.output.absolute_change, Decimal("200.00"))
        self.assertEqual(result.output.growth_rate_percent, Decimal("50.00"))
        self.assertEqual(result.output.region, "all")
        self.assertEqual(result.output.currency, "USD")
        self.assertEqual(result.state.tool_calls_used, 2)
        self.assertEqual(result.output.source_relation, "analytics.sales_orders")
        self.assertEqual(result.output.dataset_version, "synthetic-sales-db-v1")
        baseline = result.state.step_states[0].output
        comparison = result.state.step_states[1].output
        self.assertEqual(baseline["region"], comparison["region"])
        self.assertEqual(baseline["currency"], comparison["currency"])
        self.assertEqual(baseline["data_source"], comparison["data_source"])

    def test_registry_authorization_still_blocks_untrusted_principal(self):
        runtime = build_planning_runtime(
            FakePlanProvider(), settings=PlanningSettings(enabled=True)
        )
        result = runtime.run(
            PlanningRequest(),
            ToolExecutionContext(
                principal="untrusted-caller",
                correlation_id="phase5-db-denied",
            ),
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error.code, "tool_execution_failed")
        self.assertEqual(result.state.tool_calls_used, 1)
        self.assertEqual(result.state.step_states[0].status, "failed")
        self.assertEqual(result.state.step_states[1].status, "skipped")


if __name__ == "__main__":
    unittest.main()
