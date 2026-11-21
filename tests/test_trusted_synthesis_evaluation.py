from hashlib import sha256
import json
from pathlib import Path
import unittest

from backend.synthesis.evaluation import (
    SynthesisEvaluationRecord,
    SynthesisQualityScores,
    evaluate,
    release_gate,
)


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "eval" / "datasets" / "synthesis-v1"


class TrustedSynthesisEvaluationTests(unittest.TestCase):
    def test_suite_policy_and_evaluator_are_frozen(self):
        freeze = json.loads((ASSETS / "freeze-v1.json").read_text())
        cases = ASSETS / "cases-v1.json"
        policy = ASSETS / "policy-v1.json"
        evaluator = ROOT / "backend" / "synthesis" / "evaluation.py"
        self.assertEqual(sha256(cases.read_bytes()).hexdigest(), freeze["suite_sha256"])
        self.assertEqual(sha256(policy.read_bytes()).hexdigest(), freeze["policy_sha256"])
        self.assertEqual(sha256(evaluator.read_bytes()).hexdigest(), freeze["evaluator_sha256"])
        self.assertEqual(len(json.loads(cases.read_text())["cases"]), freeze["case_count"])
        self.assertEqual(freeze["semantic_quality_threshold"], 0.85)

    def record(self, case_id="answer/simple", **updates):
        values = {
            "case_id": case_id,
            "schema_valid": True,
            "hard_invariants": {
                "receipt_integrity": True,
                "authorization_and_lifecycle": True,
                "numeric_and_unit_consistency": True,
                "source_traceability": True,
                "no_unsupported_causality": True,
                "safe_output": True,
            },
            "core_task_complete": True,
            "quality": SynthesisQualityScores(completeness=2, relevance=2, clarity=2),
            "evidence_refs": ("offline:test",),
        }
        values.update(updates)
        return SynthesisEvaluationRecord(**values)

    def test_gate_requires_every_hard_schema_and_core_check(self):
        passing = self.record()
        self.assertTrue(evaluate(passing).passed)
        hard = dict(passing.hard_invariants)
        hard["source_traceability"] = False
        failing = passing.model_copy(update={"hard_invariants": hard})
        verdict = release_gate((passing, failing))
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.hard_pass_rate, 0.5)

    def test_quality_threshold_is_not_rounded_up(self):
        record = self.record(
            quality=SynthesisQualityScores(completeness=2, relevance=2, clarity=1)
        )
        self.assertAlmostEqual(evaluate(record).quality_score, 5 / 6)
        self.assertFalse(evaluate(record).passed)

    def test_offline_evidence_covers_every_frozen_case(self):
        cases = {item["id"] for item in json.loads((ASSETS / "cases-v1.json").read_text())["cases"]}
        raw = json.loads((ASSETS / "offline-evidence-v1.json").read_text())
        records = tuple(
            SynthesisEvaluationRecord.model_validate_json(
                json.dumps(item, ensure_ascii=False), strict=True
            )
            for item in raw["case_evidence"]
        )
        self.assertEqual({item.case_id for item in records}, cases)
        verdict = release_gate(records)
        self.assertTrue(verdict.passed)
        self.assertEqual(verdict.schema_pass_rate, raw["gate"]["schema_pass_rate"])
        self.assertEqual(verdict.hard_pass_rate, raw["gate"]["hard_pass_rate"])
        self.assertEqual(verdict.core_pass_rate, raw["gate"]["core_pass_rate"])
        self.assertEqual(verdict.semantic_quality, raw["gate"]["semantic_quality"])


if __name__ == "__main__":
    unittest.main()
