import json
import unittest

from eval.planning.v1.suite_v1 import ROOT, gate_sha256, suite_sha256, verify_freeze


class PlannerLiveEvidenceV1Tests(unittest.TestCase):
    def test_final_summary_matches_frozen_gate_and_rounds(self):
        summary = json.loads((ROOT / "live-summary-v1.json").read_text())
        self.assertEqual(summary["suite_sha256"], suite_sha256())
        self.assertEqual(summary["gate_sha256"], gate_sha256())
        self.assertEqual(summary["rounds_completed"], 3)
        self.assertEqual(summary["paid_api_calls"], 9)
        self.assertIsNone(summary["stop_reason"])
        self.assertEqual(summary["gate"]["verdict"], "PASS")
        self.assertEqual(summary["gate"]["blockers"], [])
        self.assertLessEqual(float(summary["estimated_cost_usd"]), 0.02)
        self.assertEqual(summary["tokens"]["total"], 10993)

    def test_all_nine_records_are_valid_authorized_complete_and_secret_free(self):
        case_ids = set()
        total_records = 0
        for index in range(1, 4):
            path = ROOT / f"live-v1-{index:03d}.json"
            payload = json.loads(path.read_text())
            self.assertEqual(payload["suite_sha256"], suite_sha256())
            self.assertEqual(payload["gate_sha256"], gate_sha256())
            self.assertEqual(payload["result"]["verdict"], "PASS")
            self.assertTrue(payload["result"]["schema_plan_authorization_100_percent"])
            self.assertEqual(payload["result"]["core_blockers"], 0)
            self.assertEqual(payload["result"]["normalized_quality"], 1.0)
            self.assertEqual(len(payload["records"]), 3)
            for record in payload["records"]:
                total_records += 1
                case_ids.add(record["case_id"])
                review = record["review"]
                self.assertEqual(review["verdict"], "PASS")
                self.assertTrue(all(review["applicable_hard_invariants"].values()))
                self.assertFalse(review["core_blocker"])
                self.assertEqual(review["normalized_quality"], 1.0)
                self.assertEqual(record["execution"]["status"], "completed")
                self.assertEqual(record["generation"]["retry_count"], 0)
            serialized = path.read_text()
            for forbidden in ('"api_key"', '"headers"', "Authorization", "Bearer "):
                self.assertNotIn(forbidden, serialized)
        self.assertEqual(total_records, 9)
        self.assertEqual(case_ids, {"valid/default", "valid/north", "completion/q1_q3"})

    def test_historical_preregistration_gate_remains_unchanged(self):
        historical = json.loads((ROOT / "gate-status-v1.json").read_text())
        self.assertEqual(historical["verdict"], "BLOCKED")
        self.assertEqual(historical["blockers"], ["minimum_live_rounds_not_met"])
        self.assertEqual(verify_freeze()["status"], "preregistered_before_paid_execution")


if __name__ == "__main__":
    unittest.main()
