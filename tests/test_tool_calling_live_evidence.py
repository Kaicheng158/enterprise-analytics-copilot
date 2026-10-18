import json
import unittest
from decimal import Decimal
from pathlib import Path

from dotenv import dotenv_values

from eval.tools.suite import (
    ToolEvalDecision,
    ToolEvalRecord,
    canonical_sha256,
    review_record,
    suite_sha256,
    verify_freeze,
)

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "eval" / "tools"


class ToolCallingLiveEvidenceTests(unittest.TestCase):
    def test_three_rounds_are_complete_hash_bound_and_gate_result_is_preserved(self):
        summary = json.loads((EVAL / "live-regression-summary.json").read_text())
        self.assertEqual(summary["suite_sha256"], suite_sha256())
        self.assertEqual(summary["rounds_completed"], 3)
        self.assertIsNone(summary["stop_reason"])
        total_cost = Decimal("0")
        for index in range(1, 4):
            directory = EVAL / f"live-{index:03d}"
            raw = json.loads((directory / "raw.json").read_text())
            decisions = json.loads((directory / "decisions.json").read_text())
            reviewed = json.loads((directory / "reviewed.json").read_text())
            self.assertEqual(raw["suite_sha256"], suite_sha256())
            self.assertEqual(len(raw["records"]), 9)
            self.assertEqual(reviewed["case_count"], 9)
            records = {
                item.case_id: item
                for item in (
                    ToolEvalRecord.model_validate(value, strict=True)
                    for value in raw["records"]
                )
            }
            parsed = [
                ToolEvalDecision.model_validate(value, strict=True)
                for value in decisions["decisions"]
            ]
            rebuilt = [review_record(records[item.case_id], item) for item in parsed]
            self.assertEqual(
                reviewed["reviewed_evidence_sha256"],
                canonical_sha256([item.model_dump(mode="json") for item in rebuilt]),
            )
            self.assertEqual(sum(item.verdict == "PASS" for item in rebuilt), 6)
            self.assertEqual(sum(item.verdict == "FAIL" for item in rebuilt), 3)
            total_cost += sum(
                Decimal(item.telemetry.estimated_cost or "0") for item in records.values()
            )
        self.assertEqual(total_cost, Decimal(summary["cumulative_estimated_cost"]))
        self.assertEqual(summary["gate"]["verdict"], "FAIL")
        self.assertEqual(summary["gate"]["blockers"], ["one_or_more_live_runs_failed"])
        self.assertEqual(verify_freeze()["live_regression_status"], "not_run")

    def test_live_evidence_is_synthetic_and_contains_no_configured_api_key(self):
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for directory in [EVAL / "live-001", EVAL / "live-002", EVAL / "live-003"]
            for path in sorted(directory.glob("*.json"))
        )
        key = dotenv_values(ROOT / ".env").get("DEEPSEEK_API_KEY")
        self.assertTrue(key)
        self.assertNotIn(key, combined)
        lowered = combined.lower()
        self.assertNotIn("authorization: bearer", lowered)
        self.assertNotIn("deepseek_api_key", lowered)
        self.assertNotIn("openai_api_key", lowered)
        for directory in [EVAL / "live-001", EVAL / "live-002", EVAL / "live-003"]:
            raw = json.loads((directory / "raw.json").read_text())
            self.assertEqual(raw["data_classification"], "synthetic_non_sensitive")
