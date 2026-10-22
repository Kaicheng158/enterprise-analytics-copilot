import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.planning.deepseek_planner_adapter import DeepSeekPlannerSettings
from backend.planning.durable_agent_runtime import DurableAgentSettings
from backend.planning.durable_controlled_runtime import DurableControlledPlannerSettings
from backend.planning.worker_runtime import DurableWorkerSettings
from eval.planning.durable_v1 import live_runner_v1
from eval.planning.durable_v1.preflight_v1 import run_preflight
from eval.planning.durable_v1.release_gate_v1 import evaluate_gate
from eval.planning.durable_v1.suite_v1 import (
    gate_sha256,
    live_plan_sha256,
    suite_sha256,
    validate_suite,
    verify_freeze,
)


class DurablePlannerLivePreparationTests(unittest.TestCase):
    def test_frozen_suite_gate_and_execution_plan_are_consistent(self):
        policy, suite, gate, plan = validate_suite()
        freeze = verify_freeze()
        self.assertEqual(len(suite["cases"]), 3)
        self.assertEqual(plan["rounds"], 2)
        self.assertEqual(plan["maximum_provider_calls_total"], 6)
        self.assertEqual(plan["maximum_reserved_cost_usd"], "0.012")
        self.assertEqual(plan["budget_limit_usd"], "0.02")
        self.assertEqual(plan["automatic_retries"], 0)
        self.assertEqual(freeze["suite_sha256"], suite_sha256())
        self.assertEqual(freeze["gate_sha256"], gate_sha256())
        self.assertEqual(freeze["live_plan_sha256"], live_plan_sha256())
        self.assertEqual(
            policy["release_rules"]["minimum_round_normalized_quality"], 0.85
        )
        self.assertEqual(gate["minimum_independent_live_rounds"], 2)

    def test_paid_runner_requires_both_server_guard_and_exact_confirmation(self):
        plan, cases = live_runner_v1._load_execution_plan()
        self.assertEqual(len(cases), 3)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(PermissionError):
                live_runner_v1._authorize_live(
                    plan, live_runner_v1.LIVE_CONFIRMATION
                )
        with patch.dict(os.environ, {live_runner_v1.LIVE_ENV: "true"}):
            with self.assertRaises(PermissionError):
                live_runner_v1._authorize_live(plan, "wrong-confirmation")

    @patch(
        "eval.planning.durable_v1.preflight_v1.load_durable_agent_settings",
        return_value=DurableAgentSettings(enabled=False),
    )
    @patch(
        "eval.planning.durable_v1.preflight_v1.load_durable_worker_settings",
        return_value=DurableWorkerSettings(enabled=False),
    )
    @patch(
        "eval.planning.durable_v1.preflight_v1.load_durable_controlled_planner_settings",
        return_value=DurableControlledPlannerSettings(enabled=False),
    )
    @patch(
        "eval.planning.durable_v1.preflight_v1.load_deepseek_planner_settings",
        return_value=DeepSeekPlannerSettings(enabled=False, api_key="test-key-not-real"),
    )
    def test_zero_paid_preflight_is_ready_and_secret_free(self, *_mocks):
        healthy = lambda: {
            "status": "healthy",
            "select_1": True,
            "dataset_rows": 8,
            "agent_runtime_schema": True,
            "tool_status": "success",
        }
        with tempfile.TemporaryDirectory() as temporary:
            report = run_preflight(
                offline_tests_passed=True,
                offline_test_count=1,
                database_tests_passed=True,
                database_test_count=1,
                database_probe=healthy,
                evidence_root=Path(temporary),
                evidence_name="unused-preflight.json",
            )
        self.assertEqual(report["status"], "READY")
        self.assertEqual(report["paid_api_calls"], 0)
        self.assertTrue(all(report["checks"].values()))
        self.assertNotIn("test-key-not-real", str(report))

    def test_release_gate_is_blocked_before_live_rounds_and_passes_only_two_clean_rounds(self):
        preflight = {"status": "READY", "suite_sha256": suite_sha256()}
        blocked = evaluate_gate(preflight, [])
        self.assertEqual(blocked["verdict"], "BLOCKED")
        passed_round = {
            "verdict": "PASS",
            "hard_invariants_100_percent": True,
            "core_blockers": 0,
            "normalized_quality": 1.0,
            "all_cases_meet_quality_threshold": True,
            "repeated_severe_quality_issue": False,
        }
        passed = evaluate_gate(preflight, [passed_round, passed_round])
        self.assertEqual(passed["verdict"], "PASS")
        failed = evaluate_gate(
            preflight,
            [passed_round, {**passed_round, "hard_invariants_100_percent": False}],
        )
        self.assertEqual(failed["verdict"], "FAIL")
        failed_case_quality = evaluate_gate(
            preflight,
            [passed_round, {**passed_round, "all_cases_meet_quality_threshold": False}],
        )
        self.assertEqual(failed_case_quality["verdict"], "FAIL")
        self.assertIn(
            "case_quality_threshold_not_met", failed_case_quality["blockers"]
        )

    def test_round_result_enforces_per_case_quality_threshold(self):
        passing_review = {
            "hard_invariants": {"safe": True},
            "core_blocker": False,
            "normalized_quality": 1.0,
        }
        records = [{"review": passing_review} for _ in range(3)]
        self.assertEqual(live_runner_v1._round_result(records)["verdict"], "PASS")
        records[0] = {
            "review": {
                **passing_review,
                "normalized_quality": 0.5,
            }
        }
        result = live_runner_v1._round_result(records)
        self.assertEqual(result["verdict"], "FAIL")
        self.assertFalse(result["all_cases_meet_quality_threshold"])


if __name__ == "__main__":
    unittest.main()
