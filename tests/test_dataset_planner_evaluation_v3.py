"""Range-aware evaluation v3 accepts equivalent scopes without weakening gates."""

from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import unittest

from backend.planning.dataset_planner_evaluation import DatasetPlannerQualityScores
from backend.planning.dataset_planner_evaluation_v3 import (
    DatasetPlannerV3Assessment,
    DatasetPlannerV3ScopeExpectation,
    filters_cover_closed_interval,
    operation_matches_compare_v2_scope,
)


class DatasetPlannerEvaluationV3Tests(unittest.TestCase):
    def setUp(self):
        self.expectation = DatasetPlannerV3ScopeExpectation(
            field="period",
            baseline_start=date(2026, 1, 1),
            baseline_end=date(2026, 1, 31),
            comparison_start=date(2026, 2, 1),
            comparison_end=date(2026, 2, 28),
            group_by=("region",),
            baseline_measure="revenue",
            comparison_measure="revenue",
        )

    def operation(self, baseline, comparison):
        return {
            "tool_name": "compare_dataset_revision_v2",
            "arguments": {
                "baseline_filters": baseline,
                "comparison_filters": comparison,
                "group_by": ["region"],
                "baseline_measure": "revenue",
                "comparison_measure": "revenue",
                "aggregation": "sum",
                "derived_metric": "absolute_change",
            },
        }

    def test_between_and_equivalent_closed_bounds_pass(self):
        between = [
            {
                "field": "period",
                "operator": "between",
                "values": ["2026-01-01", "2026-01-31"],
            }
        ]
        bounds = [
            {"field": "period", "operator": "gte", "values": ["2026-01-01"]},
            {"field": "period", "operator": "lte", "values": ["2026-01-31"]},
        ]
        self.assertTrue(
            filters_cover_closed_interval(
                between,
                field="period",
                start=date(2026, 1, 1),
                end=date(2026, 1, 31),
            )
        )
        self.assertTrue(
            filters_cover_closed_interval(
                bounds,
                field="period",
                start=date(2026, 1, 1),
                end=date(2026, 1, 31),
            )
        )

    def test_old_eq_only_and_range_string_false_positives_are_rejected(self):
        for invalid in (
            [{"field": "period", "operator": "eq", "values": ["2026-01-31"]}],
            [
                {
                    "field": "period",
                    "operator": "eq",
                    "values": ["2026-01-01..2026-01-31"],
                }
            ],
        ):
            with self.subTest(invalid=invalid):
                self.assertFalse(
                    filters_cover_closed_interval(
                        invalid,
                        field="period",
                        start=date(2026, 1, 1),
                        end=date(2026, 1, 31),
                    )
                )

    def test_operation_scope_and_runtime_core_are_independent(self):
        operation = self.operation(
            [
                {
                    "field": "period",
                    "operator": "between",
                    "values": ["2026-01-01", "2026-01-31"],
                }
            ],
            [
                {"field": "period", "operator": "gte", "values": ["2026-02-01"]},
                {"field": "period", "operator": "lte", "values": ["2026-02-28"]},
            ],
        )
        self.assertTrue(operation_matches_compare_v2_scope(operation, self.expectation))
        failed_runtime = DatasetPlannerV3Assessment(
            case_id="plan/range",
            schema_valid=True,
            authority_preserved=True,
            allowed_tool_only=True,
            scope_semantically_complete=True,
            runtime_completed=False,
            result_correct=False,
            quality=DatasetPlannerQualityScores(
                task_completeness=2, relevance=2, clarity=2
            ),
            evidence_refs=("offline://range",),
        )
        self.assertEqual(failed_runtime.verdict().verdict, "FAIL")
        passed = failed_runtime.model_copy(
            update={"runtime_completed": True, "result_correct": True}
        )
        self.assertEqual(passed.verdict().verdict, "PASS")

    def test_hard_invariant_and_quality_threshold_cannot_be_averaged_away(self):
        base = DatasetPlannerV3Assessment(
            case_id="security/injection",
            schema_valid=True,
            authority_preserved=False,
            allowed_tool_only=True,
            scope_semantically_complete=True,
            runtime_completed=True,
            result_correct=True,
            quality=DatasetPlannerQualityScores(
                task_completeness=2, relevance=2, clarity=2
            ),
            evidence_refs=("offline://security",),
        )
        self.assertEqual(base.verdict().verdict, "FAIL")
        low_quality = base.model_copy(
            update={
                "authority_preserved": True,
                "quality": DatasetPlannerQualityScores(
                    task_completeness=2, relevance=2, clarity=1
                ),
            }
        )
        self.assertEqual(low_quality.verdict().verdict, "FAIL")

    def test_v3_suite_policy_and_future_live_plan_are_hash_frozen(self):
        root = Path(__file__).resolve().parents[1]
        directory = root / "eval/datasets/planner-v3"
        freeze = json.loads((directory / "freeze-v3.json").read_text())
        for filename, key in (
            ("cases-v3.json", "suite_sha256"),
            ("policy-v3.json", "policy_sha256"),
            ("live-plan-v3.json", "live_plan_sha256"),
        ):
            self.assertEqual(
                sha256((directory / filename).read_bytes()).hexdigest(), freeze[key]
            )
        plan = json.loads((directory / "live-plan-v3.json").read_text())
        self.assertEqual(plan["target_stage"], "PV2-04d.4")
        self.assertEqual(plan["maximum_provider_calls_total"], 4)
        self.assertEqual(plan["automatic_retries"], 0)
        self.assertEqual(plan["budget_limit_usd"], "0.01")
        self.assertFalse(plan["paid_execution_authorized"])
        self.assertEqual(plan["prompt_sha256"], freeze["prompt_sha256"])
        self.assertEqual(
            plan["response_schema_sha256"], freeze["response_schema_sha256"]
        )


if __name__ == "__main__":
    unittest.main()
