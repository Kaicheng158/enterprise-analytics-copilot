import json
import unittest
from pathlib import Path

from eval.tools.suite import (
    ToolEvalDecision,
    ToolEvalRecord,
    canonical_sha256,
    review_record,
    suite_sha256,
)

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "eval" / "tools" / "deterministic-controls-001"


class ToolCallingControlEvidenceTests(unittest.TestCase):
    def test_formal_control_evidence_is_complete_bound_and_passing(self):
        raw = json.loads((EVIDENCE / "raw.json").read_text(encoding="utf-8"))
        decisions = json.loads(
            (EVIDENCE / "decisions.json").read_text(encoding="utf-8")
        )
        reviewed = json.loads(
            (EVIDENCE / "reviewed.json").read_text(encoding="utf-8")
        )
        gate = json.loads(
            (EVIDENCE / "gate-status.json").read_text(encoding="utf-8")
        )
        self.assertEqual(raw["suite_sha256"], suite_sha256())
        self.assertEqual(raw["paid_api_calls"], 0)
        self.assertEqual(len(raw["records"]), 7)
        self.assertEqual(reviewed["case_count"], 7)
        self.assertEqual(reviewed["pass_count"], 7)
        self.assertEqual(reviewed["fail_count"], 0)
        records = {
            item.case_id: item
            for item in (
                ToolEvalRecord.model_validate(value, strict=True)
                for value in raw["records"]
            )
        }
        parsed_decisions = [
            ToolEvalDecision.model_validate(value, strict=True)
            for value in decisions["decisions"]
        ]
        rebuilt = [review_record(records[item.case_id], item) for item in parsed_decisions]
        self.assertTrue(all(item.verdict == "PASS" for item in rebuilt))
        self.assertEqual(
            reviewed["reviewed_evidence_sha256"],
            canonical_sha256([item.model_dump(mode="json") for item in rebuilt]),
        )
        self.assertEqual(gate["control_result"]["verdict"], "PASS")
        self.assertEqual(gate["control_result"]["core_blockers"], 0)
        self.assertTrue(gate["control_result"]["hard_invariants_pass"])
        self.assertEqual(gate["verdict"], "NOT_RUN")
        self.assertEqual(gate["blockers"], ["minimum_live_runs_not_met"])

    def test_evidence_is_redacted_and_contains_required_safe_diagnostics(self):
        combined = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted(EVIDENCE.glob("*.json"))
        )
        lowered = combined.lower()
        for forbidden in (
            "synthetic-control-key-never-persisted",
            "synthetic private database detail",
            "synthetic transport detail",
            "authorization: bearer",
            "openai_api_key",
            "deepseek_api_key",
        ):
            self.assertNotIn(forbidden, lowered)
        raw = json.loads((EVIDENCE / "raw.json").read_text(encoding="utf-8"))
        codes = {item["telemetry"]["error_code"] for item in raw["records"]}
        self.assertIn("tool_provider_invalid_arguments", codes)
        self.assertIn("tool_database_error", codes)
        self.assertIn("tool_provider_timeout", codes)
        self.assertIn("tool_provider_multiple_calls", codes)
        self.assertIn("tool_call_limit_exceeded", codes)
