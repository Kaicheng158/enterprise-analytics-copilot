import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.planning.demo import build_quarter_sales_comparison_plan
from backend.planning.models import PlanExecutionResult, PlanningRequest
from eval.planning.v1 import live_runner_v1


class PlannerLiveRunnerV1Tests(unittest.TestCase):
    def test_frozen_mapping_is_exactly_three_by_three(self):
        plan, cases = live_runner_v1._load_live_plan()
        self.assertEqual(len(cases), 3)
        self.assertEqual(plan["rounds"], 3)
        self.assertEqual(plan["maximum_provider_calls_total"], 9)
        self.assertEqual(
            [case["id"] for case in cases],
            ["valid/default", "valid/north", "completion/q1_q3"],
        )

    def test_paid_mode_requires_server_guard_and_exact_confirmation(self):
        plan, _ = live_runner_v1._load_live_plan()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(PermissionError):
                live_runner_v1._authorize_live(plan, live_runner_v1.LIVE_CONFIRMATION)
        with patch.dict(os.environ, {live_runner_v1.LIVE_ENV: "true"}):
            with self.assertRaises(PermissionError):
                live_runner_v1._authorize_live(plan, "wrong")

    def test_evidence_writer_refuses_overwrite_and_wrong_parent(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "evidence.json"
            with self.assertRaises(FileExistsError):
                live_runner_v1._write_json(target, {})

    def test_review_rejects_missing_execution_even_with_well_formed_plan(self):
        request = PlanningRequest()
        plan = build_quarter_sales_comparison_plan(request)
        result = PlanExecutionResult(
            status="failed",
            plan=plan,
            state=None,
            state_history=(),
            output=None,
            error={"code": "tool_execution_failed", "message": "failed"},
            provider_calls=1,
        )
        provider = type("Provider", (), {"calls": 1})()
        case = {"id": "valid/default", "hard_invariants": ["expected_outcome"]}
        review = live_runner_v1._review(case, request, result, provider)
        self.assertEqual(review["verdict"], "FAIL")
        self.assertTrue(review["core_blocker"])
        self.assertEqual(review["quality"]["task_completeness"], 0)


if __name__ == "__main__":
    unittest.main()
