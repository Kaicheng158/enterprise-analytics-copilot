import unittest
from copy import deepcopy

from backend.planning import (
    PlanValidator,
    PlanningLimits,
    PlanningPolicy,
    PlanningRequest,
    build_quarter_sales_comparison_plan,
    canonical_plan_sha256,
)
from backend.planning.models import Plan


class PlanValidationTests(unittest.TestCase):
    def setUp(self):
        self.plan = build_quarter_sales_comparison_plan(PlanningRequest())

    def plan_with(self, mutate):
        payload = deepcopy(self.plan.model_dump(mode="python"))
        mutate(payload)
        return Plan.model_validate(payload, strict=True)

    def test_valid_plan_has_stable_hash_and_declared_execution_order(self):
        first = PlanValidator().validate(self.plan)
        second = PlanValidator().validate(self.plan)
        self.assertEqual(first.status, "valid")
        self.assertEqual(first.error, None)
        self.assertEqual(first.plan_sha256, second.plan_sha256)
        self.assertEqual(first.plan_sha256, canonical_plan_sha256(self.plan))
        self.assertEqual(
            first.execution_order,
            (
                "load_baseline_sales",
                "load_comparison_sales",
                "calculate_growth",
            ),
        )

        reordered_payload = deepcopy(self.plan.model_dump(mode="python"))
        arguments = reordered_payload["steps"][0]["tool_call"]["arguments"]
        reordered_payload["steps"][0]["tool_call"]["arguments"] = {
            "region": arguments["region"],
            "period": arguments["period"],
        }
        reordered = Plan.model_validate(reordered_payload, strict=True)
        self.assertEqual(first.plan_sha256, canonical_plan_sha256(reordered))

    def test_unknown_dependency_is_rejected(self):
        plan = self.plan_with(
            lambda payload: payload["steps"][0].update(
                depends_on=("missing_step",)
            )
        )
        result = PlanValidator().validate(plan)
        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.error.code, "plan_dependency_unknown")
        self.assertEqual(result.execution_order, ())

    def test_two_node_and_self_cycles_are_rejected(self):
        def two_node(payload):
            payload["steps"][0]["depends_on"] = ("load_comparison_sales",)
            payload["steps"][1]["depends_on"] = ("load_baseline_sales",)

        def self_cycle(payload):
            payload["steps"][0]["depends_on"] = ("load_baseline_sales",)

        for plan in (self.plan_with(two_node), self.plan_with(self_cycle)):
            with self.subTest(plan=plan):
                result = PlanValidator().validate(plan)
                self.assertEqual(result.status, "invalid")
                self.assertEqual(result.error.code, "plan_cycle_detected")

    def test_acyclic_forward_dependency_is_rejected_as_out_of_order(self):
        plan = self.plan_with(
            lambda payload: payload["steps"][0].update(
                depends_on=("load_comparison_sales",)
            )
        )
        result = PlanValidator().validate(plan)
        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.error.code, "plan_dependency_order_invalid")

    def test_tool_arguments_must_match_bound_task_scope(self):
        def wrong_period(payload):
            payload["steps"][0]["tool_call"]["arguments"]["period"] = "2026-Q3"

        def extra_argument(payload):
            payload["steps"][1]["tool_call"]["arguments"]["currency"] = "USD"

        for plan in (self.plan_with(wrong_period), self.plan_with(extra_argument)):
            with self.subTest(plan=plan):
                result = PlanValidator().validate(plan)
                self.assertEqual(result.status, "invalid")
                self.assertEqual(result.error.code, "plan_action_mismatch")

    def test_limits_and_server_policy_are_independent_hard_failures(self):
        limited = PlanValidator(limits=PlanningLimits(max_steps=2)).validate(
            self.plan
        )
        self.assertEqual(limited.error.code, "step_limit_exceeded")

        unauthorized = PlanValidator(
            policy=PlanningPolicy(allowed_tools=frozenset())
        ).validate(self.plan)
        self.assertEqual(unauthorized.error.code, "action_not_authorized")


if __name__ == "__main__":
    unittest.main()
