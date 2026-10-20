import os
import json
import unittest
from datetime import UTC, datetime
from unittest.mock import Mock, patch

from backend.main import app
from backend.planning import (
    DurableControlledPlanner,
    DurableControlledPlannerSettings,
    DurablePlannerError,
    PlanningRequest,
    load_durable_controlled_planner_settings,
)
from backend.planning.demo import build_quarter_sales_comparison_plan


class DurableControlledPlannerContractTests(unittest.TestCase):
    def test_feature_flag_defaults_off_and_rejects_invalid_value(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(load_durable_controlled_planner_settings().enabled)
        with patch.dict(os.environ, {"DURABLE_LLM_PLANNER_ENABLED": "invalid"}):
            with self.assertRaisesRegex(ValueError, "Invalid DURABLE"):
                load_durable_controlled_planner_settings()

    def test_disabled_runtime_and_public_route_absence(self):
        with self.assertRaisesRegex(ValueError, "durable_llm_planner_disabled"):
            DurableControlledPlanner(
                Mock(),
                Mock(),
                Mock(),
                settings=DurableControlledPlannerSettings(enabled=False),
            )
        paths = {getattr(route, "path", "") for route in app.routes}
        self.assertFalse(any("plan" in path for path in paths))

    def test_invalid_provider_plan_fails_before_persistence(self):
        store = Mock()
        store.find_execution_by_idempotency.return_value = None
        provider = Mock()
        provider.propose.return_value = "not-json"
        runtime = DurableControlledPlanner(
            provider,
            store,
            Mock(),
            settings=DurableControlledPlannerSettings(enabled=True),
        )
        with self.assertRaisesRegex(DurablePlannerError, "plan_invalid"):
            runtime.submit(
                PlanningRequest(),
                scope_key="tenant-a:analytics-copilot",
                idempotency_key="offline-invalid-plan",
                now=datetime(2026, 10, 20, tzinfo=UTC),
            )
        store.create_or_get_execution.assert_not_called()

    def test_unauthorized_generated_tool_is_rejected_before_persistence(self):
        request = PlanningRequest()
        payload = build_quarter_sales_comparison_plan(request).model_dump(mode="json")
        payload["steps"][0]["tool_call"]["name"] = "get_sales_summary"
        store = Mock()
        store.find_execution_by_idempotency.return_value = None
        provider = Mock()
        provider.propose.return_value = json.dumps(payload)
        runtime = DurableControlledPlanner(
            provider,
            store,
            Mock(),
            settings=DurableControlledPlannerSettings(enabled=True),
        )
        with self.assertRaisesRegex(DurablePlannerError, "action_not_authorized"):
            runtime.submit(
                request,
                scope_key="tenant-a:analytics-copilot",
                idempotency_key="offline-unauthorized-plan",
                now=datetime(2026, 10, 20, tzinfo=UTC),
            )
        store.create_or_get_execution.assert_not_called()

    def test_provider_exception_is_sanitized_and_naive_time_fails_before_provider(self):
        store = Mock()
        store.find_execution_by_idempotency.return_value = None
        provider = Mock()
        provider.propose.side_effect = RuntimeError("private upstream detail")
        runtime = DurableControlledPlanner(
            provider,
            store,
            Mock(),
            settings=DurableControlledPlannerSettings(enabled=True),
        )
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            runtime.submit(
                PlanningRequest(),
                scope_key="tenant-a:analytics-copilot",
                idempotency_key="offline-naive-time",
                now=datetime(2026, 10, 20),
            )
        provider.propose.assert_not_called()
        with self.assertRaisesRegex(DurablePlannerError, "provider_error") as caught:
            runtime.submit(
                PlanningRequest(),
                scope_key="tenant-a:analytics-copilot",
                idempotency_key="offline-provider-error",
                now=datetime(2026, 10, 20, tzinfo=UTC),
            )
        self.assertNotIn("private upstream detail", str(caught.exception))
        store.create_or_get_execution.assert_not_called()


if __name__ == "__main__":
    unittest.main()
