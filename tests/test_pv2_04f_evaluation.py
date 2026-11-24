from hashlib import sha256
import json
from pathlib import Path
import unittest

from backend.synthesis.integration_evaluation import (
    IntegrationCaseRecord,
    IntegrationQualityScores,
    evaluate_case,
    integration_release_gate,
)


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "eval" / "datasets" / "integration-v1"


class PV204FIntegrationEvaluationTests(unittest.TestCase):
    def load_evidence(self):
        raw = json.loads((ASSETS / "offline-evidence-v1.json").read_text())
        defaults = raw["record_defaults"]
        records = tuple(
            IntegrationCaseRecord.model_validate_json(
                json.dumps(
                    {
                        **defaults,
                        **item,
                        "version_refs": raw["common_version_refs"],
                        "sha_refs": raw["common_sha_refs"],
                    },
                    ensure_ascii=False,
                ),
                strict=True,
            )
            for item in raw["case_records"]
        )
        return raw, records

    def test_suite_policy_evaluator_and_referenced_releases_are_frozen(self):
        freeze = json.loads((ASSETS / "freeze-v1.json").read_text())
        paths = {
            "suite_sha256": ASSETS / "cases-v1.json",
            "policy_sha256": ASSETS / "policy-v1.json",
            "evaluator_sha256": ROOT / "backend" / "synthesis" / "integration_evaluation.py",
            "trusted_synthesis_freeze_sha256": ROOT / "eval" / "datasets" / "synthesis-v1" / "freeze-v1.json",
            "dataset_planner_v3_freeze_sha256": ROOT / "eval" / "datasets" / "planner-v3" / "freeze-v3.json",
        }
        for key, path in paths.items():
            with self.subTest(key=key):
                self.assertEqual(sha256(path.read_bytes()).hexdigest(), freeze[key])
        cases = json.loads((ASSETS / "cases-v1.json").read_text())["cases"]
        self.assertEqual((len(cases), freeze["case_count"]), (15, 15))
        self.assertEqual(freeze["semantic_quality_threshold"], 0.85)
        self.assertEqual(freeze["new_paid_provider_calls"], 0)

    def test_saved_evidence_reproduces_preregistered_gate_pass(self):
        raw, records = self.load_evidence()
        case_ids = {
            item["id"]
            for item in json.loads((ASSETS / "cases-v1.json").read_text())["cases"]
        }
        self.assertEqual({item.case_id for item in records}, case_ids)
        gate = integration_release_gate(records)
        self.assertTrue(gate.passed)
        for field in (
            "case_count",
            "schema_pass_rate",
            "hard_pass_rate",
            "core_pass_rate",
            "semantic_quality",
            "unexpected_failure_count",
        ):
            self.assertEqual(getattr(gate, field), raw["gate"][field])

    def test_new_evaluation_makes_no_paid_call_and_live_usage_is_historical_only(self):
        raw, records = self.load_evidence()
        self.assertEqual(raw["new_paid_provider_calls"], 0)
        live = [item for item in records if item.provider_calls]
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0].planner_mode, "frozen_live_deepseek_reference")
        self.assertEqual((live[0].provider_calls, live[0].tokens), (4, 14044))
        self.assertTrue(all(item.synthesizer_mode == "fake" for item in records))

    def test_one_hard_or_core_failure_cannot_be_averaged_away(self):
        _, records = self.load_evidence()
        hard = dict(records[0].hard_invariants)
        hard["synthesis_grounding"] = False
        hard_failure = records[0].model_copy(update={"hard_invariants": hard})
        gate = integration_release_gate((hard_failure, *records[1:]))
        self.assertFalse(gate.passed)
        self.assertLess(gate.hard_pass_rate, 1)
        core_failure = records[0].model_copy(update={"core_task_complete": False})
        self.assertFalse(integration_release_gate((core_failure, *records[1:])).passed)

    def test_semantic_threshold_is_exact_and_unexpected_outcome_is_classified(self):
        _, records = self.load_evidence()
        low_quality = records[0].model_copy(
            update={
                "quality": IntegrationQualityScores(
                    task_completion=2, relevance_actionability=2, clarity_concision=1
                )
            }
        )
        self.assertAlmostEqual(evaluate_case(low_quality).quality_score, 5 / 6)
        self.assertFalse(evaluate_case(low_quality).passed)
        with self.assertRaises(ValueError):
            IntegrationCaseRecord.model_validate(
                {
                    **records[0].model_dump(mode="python"),
                    "observed_outcome": "blocked",
                    "failure_class": None,
                },
                strict=True,
            )


if __name__ == "__main__":
    unittest.main()
