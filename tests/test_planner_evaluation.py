import copy
import json
import unittest
from pathlib import Path

from eval.planning.v1.release_gate_v1 import evaluate_gate
from eval.planning.v1.suite_v1 import (
    FREEZE_PATH,
    ROOT,
    gate_sha256,
    load_gate,
    load_policy,
    load_suite,
    suite_sha256,
    validate_suite,
    verify_freeze,
)


class PlannerEvaluationV1Tests(unittest.TestCase):
    def setUp(self):
        self.evidence = json.loads((ROOT / "controls-v1-001.json").read_text())

    def test_frozen_suite_policy_gate_and_hashes_are_valid(self):
        policy, suite = validate_suite()
        freeze = verify_freeze()
        self.assertEqual(len(suite["cases"]), 10)
        self.assertEqual(freeze["suite_sha256"], suite_sha256())
        self.assertEqual(freeze["gate_sha256"], gate_sha256())
        self.assertEqual(policy["release_rules"]["minimum_case_normalized_quality"], 0.85)
        self.assertEqual(load_gate()["minimum_independent_live_rounds"], 3)

    def test_control_evidence_is_complete_hash_bound_and_all_pass(self):
        self.assertEqual(self.evidence["suite_sha256"], suite_sha256())
        self.assertEqual(self.evidence["gate_sha256"], gate_sha256())
        self.assertEqual(self.evidence["paid_api_calls"], 0)
        self.assertEqual(len(self.evidence["records"]), 10)
        self.assertTrue(all(item["verdict"] == "PASS" for item in self.evidence["records"]))
        self.assertTrue(all(all(item["hard_invariants"].values()) for item in self.evidence["records"]))

    def test_gate_is_blocked_only_by_missing_live_rounds(self):
        result = evaluate_gate(self.evidence, [])
        self.assertEqual(result["verdict"], "BLOCKED")
        self.assertEqual(result["blockers"], ["minimum_live_rounds_not_met"])
        self.assertEqual(result["control_passes"], 10)

    def test_control_failure_cannot_be_averaged_away(self):
        changed = copy.deepcopy(self.evidence)
        changed["records"][0]["verdict"] = "FAIL"
        result = evaluate_gate(changed, [])
        self.assertEqual(result["verdict"], "FAIL")
        self.assertIn("deterministic_controls_failed", result["blockers"])

    def test_live_plan_budget_and_stop_rules_are_preregistered(self):
        plan = json.loads((ROOT / "live-plan-v1.json").read_text())
        self.assertEqual(plan["maximum_provider_calls_total"], 9)
        self.assertEqual(plan["automatic_retries"], 0)
        self.assertLessEqual(float(plan["maximum_reserved_cost_usd"]), float(plan["budget_limit_usd"]))
        self.assertFalse(plan["public_api_enabled"])
        self.assertGreaterEqual(len(plan["stop_conditions"]), 6)


if __name__ == "__main__":
    unittest.main()
