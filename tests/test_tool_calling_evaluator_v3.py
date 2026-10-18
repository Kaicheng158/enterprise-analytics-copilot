import hashlib
import json
import unittest
from pathlib import Path

from backend.tools.orchestration import ToolOrchestrationResult
from eval.tools.suite import ToolEvalRecord, canonical_sha256
from eval.tools.v2.suite_v2 import suite_sha256_v2, verify_freeze_v2
from eval.tools.v3.evaluator_v3 import (
    evaluate_record_v3,
    extract_business_numeric_claims,
    grounding_diagnostics,
)
from eval.tools.v3.release_gate_v3 import gate_sha256_v3, load_gate_v3
from eval.tools.v3.replay_live_v2_v3 import build_replay_payload
from eval.tools.v3.suite_v3 import (
    load_cases_v3,
    load_policy_v3,
    suite_sha256_v3,
    validate_suite_v3,
    verify_freeze_v3,
)

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "eval" / "tools" / "v2"
V3 = ROOT / "eval" / "tools" / "v3"


class ToolCallingEvaluatorV3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {case["id"]: case for case in load_cases_v3()["cases"]}

    def _v2_evidence(self, case_id="grounding/tool_result", run_id="live-v2-001"):
        raw = json.loads((V2 / run_id / "raw.json").read_text(encoding="utf-8"))
        item = next(record for record in raw["records"] if record["case_id"] == case_id)
        record = ToolEvalRecord.model_validate(item, strict=True)
        result = ToolOrchestrationResult.model_validate(record.result["orchestration"], strict=True)
        return record, result

    def _evaluate_text(self, text):
        record, result = self._v2_evidence()
        modified = result.model_copy(update={"final_response": text})
        return evaluate_record_v3(
            self.cases[record.case_id], modified, record,
            purpose="historical_diagnostic_replay",
        )

    def test_v3_is_independently_versioned_and_thresholds_are_unchanged(self):
        policy, suite = validate_suite_v3()
        v2_policy = json.loads((V2 / "policy-v2.json").read_text(encoding="utf-8"))
        self.assertEqual(policy["policy_version"], "tool-calling-evaluation-policy-v3")
        self.assertEqual(suite["suite_version"], "tool-calling-suite-v3")
        self.assertEqual(len(suite["cases"]), 16)
        self.assertNotEqual(suite_sha256_v3(), suite_sha256_v2())
        self.assertEqual(policy["release_rules"], v2_policy["release_rules"])
        gate = load_gate_v3()
        self.assertEqual(gate["suite_sha256"], suite_sha256_v3())
        self.assertEqual(gate["minimum_case_normalized_quality"], 0.85)
        self.assertEqual(gate["minimum_round_normalized_quality"], 0.85)

    def test_markdown_currency_newlines_and_equivalent_labels_are_typed(self):
        text = """
        - **Total sales:** **$1,234.50 USD**
        - **Orders:**\n  **2**
        - **Avg. order amount:** `US$617.25`
        Revenue total = 1,234.50 USD
        """
        claims, _ = extract_business_numeric_claims(text)
        observed = {(claim.category, claim.value) for claim in claims}
        self.assertIn(("total_sales", "1234.5"), observed)
        self.assertIn(("order_count", "2"), observed)
        self.assertIn(("average_order_value", "617.25"), observed)
        self.assertGreaterEqual(sum(item == ("total_sales", "1234.5") for item in observed), 1)

    def test_all_nine_markdown_failures_become_complete_without_changing_raw_files(self):
        before = {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for run_id in ("live-v2-001", "live-v2-002", "live-v2-003")
            for path in (V2 / run_id).glob("*.json")
        }
        target_ids = {
            "selection/postgres_sales_summary",
            "selection/in_memory_sales_summary",
            "grounding/tool_result",
        }
        for run_id in ("live-v2-001", "live-v2-002", "live-v2-003"):
            raw = json.loads((V2 / run_id / "raw.json").read_text(encoding="utf-8"))
            for item in raw["records"]:
                if item["case_id"] not in target_ids:
                    continue
                record = ToolEvalRecord.model_validate(item, strict=True)
                result = ToolOrchestrationResult.model_validate(record.result["orchestration"], strict=True)
                reviewed = evaluate_record_v3(
                    self.cases[record.case_id], result, record,
                    purpose="historical_diagnostic_replay",
                )
                self.assertTrue(reviewed.decision.diagnostics.completion_pass)
                self.assertEqual(reviewed.verdict, "PASS")
                self.assertFalse(reviewed.release_eligible)
        after = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in before}
        self.assertEqual(before, after)

    def test_wrong_amount_still_fails_result_grounding(self):
        reviewed = self._evaluate_text(
            "**Order count:** 2\n**Total sales:** **$999.00**\n"
            "**Average order value:** $150.00"
        )
        unsupported = {
            (claim.category, claim.value)
            for claim in reviewed.decision.diagnostics.grounding.unsupported_business_claims
        }
        self.assertIn(("total_sales", "999"), unsupported)
        self.assertFalse(reviewed.decision.hard_invariants["result_grounding"])
        self.assertEqual(reviewed.verdict, "FAIL")

    def test_wrong_aov_and_wrong_order_count_still_fail(self):
        for text, expected in (
            (
                "Order count: 2\nTotal sales: $300.00\nAverage order value: $999.00",
                ("average_order_value", "999"),
            ),
            (
                "Orders: 999\nTotal revenue: USD 300.00\nAOV: 150.00 USD",
                ("order_count", "999"),
            ),
        ):
            reviewed = self._evaluate_text(text)
            unsupported = {
                (claim.category, claim.value)
                for claim in reviewed.decision.diagnostics.grounding.unsupported_business_claims
            }
            self.assertIn(expected, unsupported)
            self.assertEqual(reviewed.verdict, "FAIL")

    def test_missing_required_metric_still_fails_completion(self):
        for text in (
            "Orders: 2\nAverage order value: $150.00",
            "Total sales: $300.00\nAverage order value: $150.00",
            "Orders: 2\nTotal sales: $300.00",
        ):
            reviewed = self._evaluate_text(text)
            self.assertFalse(reviewed.decision.diagnostics.completion_pass)
            self.assertTrue(reviewed.decision.hard_invariants["result_grounding"])
            self.assertEqual(reviewed.verdict, "FAIL")

    def test_extra_fabricated_currency_amount_still_fails(self):
        reviewed = self._evaluate_text(
            "Orders: 2\nTotal sales: $300.00\nAverage order value: $150.00\n"
            "Unverified bonus: **$999.00**"
        )
        unsupported = {
            (claim.category, claim.value)
            for claim in reviewed.decision.diagnostics.grounding.unsupported_business_claims
        }
        self.assertIn(("unclassified_currency_amount", "999"), unsupported)
        self.assertFalse(reviewed.decision.hard_invariants["result_grounding"])
        self.assertEqual(reviewed.verdict, "FAIL")

    def test_context_identifiers_do_not_become_business_amounts(self):
        _, result = self._v2_evidence()
        case = self.cases["grounding/tool_result"]
        modified = result.model_copy(update={
            "final_response": (
                "2026-Q2; tool v1.2.3; latency 45 ms; 200 tokens; "
                "call 12345678-1234-1234-1234-123456789012. "
                "Orders: 2; total sales: USD 300; AOV: USD 150."
            )
        })
        diagnostics = grounding_diagnostics(case, modified)
        self.assertEqual(diagnostics.unsupported_business_claims, ())
        ignored = {item.category for item in diagnostics.ignored_numeric_context}
        self.assertTrue({"period", "version", "latency", "token_usage", "uuid"} <= ignored)

    def test_read_only_replay_covers_27_and_preserves_original_v2_verdicts(self):
        payload = build_replay_payload()
        self.assertEqual(payload["summary"], {
            "records": 27,
            "v2_pass": 18,
            "v2_fail": 9,
            "diagnostic_v3_pass": 27,
            "diagnostic_v3_fail": 0,
            "verdict_changes": 9,
            "changed_case_ids": [
                "grounding/tool_result",
                "selection/in_memory_sales_summary",
                "selection/postgres_sales_summary",
            ],
            "paid_provider_calls": 0,
        })
        self.assertTrue(all(not item["v3"]["release_eligible"] for item in payload["entries"]))
        saved = json.loads((V3 / "historical-live-v2-replay-v3.json").read_text(encoding="utf-8"))
        evidence_hash = saved.pop("replay_evidence_sha256")
        self.assertEqual(canonical_sha256(saved), evidence_hash)

    def test_v1_v2_protection_and_v3_freeze_verify(self):
        verify_freeze_v2()
        freeze = verify_freeze_v3()
        self.assertEqual(freeze["suite_sha256"], suite_sha256_v3())
        self.assertEqual(freeze["gate_canonical_sha256"], gate_sha256_v3())
        protection = json.loads((V3 / "historical-protection-v3.json").read_text(encoding="utf-8"))
        for relative, expected in protection["files"].items():
            self.assertEqual(hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(), expected)


if __name__ == "__main__":
    unittest.main()
