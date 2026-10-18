import hashlib
import inspect
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from backend.tools.orchestration import ToolOrchestrationResult
from eval.tools.suite import QualityScore, ToolEvalRecord, build_record, verify_freeze
from eval.tools.v2.evaluator_v2 import (
    aov_definition_is_correct,
    evaluate_record_v2,
    grounding_diagnostics,
)
import eval.tools.v2.release_gate_v2 as gate_module
from eval.tools.v2.release_gate_v2 import (
    build_prerequisite_evidence,
    evaluate_release_gate_v2,
    verify_prerequisites,
)
from eval.tools.v2.replay_history_v2 import build_replay_payload
from eval.tools.v2.suite_v2 import (
    EvaluationDiagnosticsV2,
    GroundingDiagnosticsV2,
    ToolEvalDecisionV2,
    load_cases_v2,
    review_record_v2,
    suite_sha256_v2,
    validate_suite_v2,
    verify_freeze_v2,
)

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "eval" / "tools"
V2 = EVAL / "v2"
PROMPT_SHA = "af1574d65979199045b6ab2cb8267fae23a3bb1887e48d1a53e51ee9ec682dfb"


class ToolCallingEvaluatorV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {case["id"]: case for case in load_cases_v2()["cases"]}

    def _historical(self, run_id, case_id):
        raw = json.loads((EVAL / run_id / "raw.json").read_text())
        data = next(item for item in raw["records"] if item["case_id"] == case_id)
        record = ToolEvalRecord.model_validate(data, strict=True)
        result = ToolOrchestrationResult.model_validate(record.result["orchestration"], strict=True)
        return record, result

    def _synthetic_review(self, case, run_id, mode="live", scores=None):
        config = {
            "temperature": 0.2, "thinking": "disabled", "max_output_tokens": 512,
            "max_provider_interactions": 2, "max_tool_executions": 1,
            "automatic_retries": 0,
        }
        record = build_record(
            run_id=run_id, case_id=case["id"], mode=mode,
            suite_sha256=suite_sha256_v2(), prompt_version="deepseek-tool-decision-v1",
            prompt_sha256=PROMPT_SHA, user_message=case["user_message"], result={},
            telemetry={
                "provider": "deepseek" if mode == "live" else "fake",
                "model": "deepseek-flash" if mode == "live" else "fake-tool-provider",
                "generation_config": config,
                "selected_tool": None, "arguments_valid": None,
                "authorization": "not_applicable", "execution_status": "not_executed",
                "provider_interactions": 1, "tool_executions": 0,
                "input_tokens": 10, "output_tokens": 2, "total_tokens": 12,
                "latency_ms": 1, "estimated_cost": "0", "cost_complete": True,
                "retry_count": 0, "error_code": None,
            },
        )
        values = scores or {name: 2 for name in case["quality_dimensions"]}
        decision = ToolEvalDecisionV2(
            purpose="release", case_id=case["id"], evidence_sha256=record.evidence_sha256,
            source_suite_sha256=suite_sha256_v2(), evaluator_suite_sha256=suite_sha256_v2(),
            hard_invariants={name: True for name in case["hard_invariants"]},
            core_blocker=False,
            quality={name: QualityScore(score=values[name], rationale="Synthetic gate test.") for name in case["quality_dimensions"]},
            rationale="Synthetic hash-bound release-gate fixture.",
            diagnostics=EvaluationDiagnosticsV2(
                authorization_pass=True, tool_source_policy_pass=True,
                aov_definition_pass=None,
                grounding=GroundingDiagnosticsV2(
                    business_claims=(), ignored_numeric_context=(), unsupported_business_claims=(),
                ),
                completion_pass=True, classification_notes=("Synthetic gate fixture.",),
            ),
        )
        return review_record_v2(record, decision)

    def _group(self, run_id, track):
        mode = "live" if track == "live_model" else "offline_fake"
        return [
            self._synthetic_review(case, run_id, mode)
            for case in self.cases.values() if case["execution_track"] == track
        ]

    def _prerequisites(self, directory, project_root):
        checks = []
        details = {
            "offline_tests": {"exit_code": 0, "tests_run": 1, "failures": 0, "errors": 0},
            "database_tests": {"exit_code": 0, "tests_run": 1, "failures": 0, "errors": 0},
            "feature_flag_default_off": {"setting": "TOOL_CALLING_ENABLED", "default_value": False},
            "public_tool_api_absent": {"public_tool_routes": []},
        }
        for name in details:
            path = directory / f"{name}.txt"
            if name in {"offline_tests", "database_tests"}:
                path.write_text("Ran 1 test in 0.001s\n\nOK\n")
            else:
                path.write_text(json.dumps({
                    "feature_flag": {"default_value": False, "effective_enabled": False},
                    "route_surface": {"public_tool_routes": []},
                }, sort_keys=True))
            checks.append({
                "name": name, "status": "PASS",
                "artifact_path": str(path.relative_to(project_root)),
                "artifact_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "details": details[name],
            })
        return build_prerequisite_evidence(checks)

    def test_v2_is_separate_and_v1_freeze_still_verifies(self):
        policy, suite = validate_suite_v2()
        self.assertEqual(len(suite["cases"]), 16)
        self.assertEqual(policy["policy_version"], "tool-calling-evaluation-policy-v2")
        self.assertNotEqual(suite_sha256_v2(), verify_freeze()["suite_sha256"])
        self.assertEqual(verify_freeze()["suite_sha256"], "4f0c8dcfe75dae98d06deac25fff6a2ce1cb743a5f3d5f7d0bd1f2553ad20958")

    def test_v2_freeze_and_v1_protection_hashes_verify(self):
        freeze = verify_freeze_v2()
        self.assertEqual(freeze["suite_sha256"], suite_sha256_v2())
        protection = json.loads((V2 / "v1-protection.json").read_text())
        for relative, expected in protection["files"].items():
            self.assertEqual(hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(), expected)

    def test_aov_semantics_accept_revenue_or_sales_and_reject_wrong_formulas(self):
        accepted = [
            "Average order value is total revenue divided by the number of orders.",
            "AOV means the average amount of money spent per order.",
            "Average order value (AOV) is total sales / orders.",
        ]
        rejected = [
            "Average order value is orders divided by revenue.",
            "AOV is total sales.",
            "Average order value is the number of orders.",
            "AOV is revenue multiplied by orders.",
        ]
        self.assertTrue(all(aov_definition_is_correct(value) for value in accepted))
        self.assertTrue(all(not aov_definition_is_correct(value) for value in rejected))

    def test_historical_aov_responses_pass_v2_without_changing_v1(self):
        for run_id in ("live-001", "live-002", "live-003"):
            record, result = self._historical(run_id, "selection/no_tool_answer")
            reviewed = evaluate_record_v2(self.cases[record.case_id], result, record, purpose="historical_diagnostic_replay")
            self.assertEqual(reviewed.verdict, "PASS")
            self.assertTrue(reviewed.decision.diagnostics.aov_definition_pass)
            self.assertFalse(reviewed.release_eligible)

    def test_authorization_and_tool_source_policy_are_independent(self):
        record, result = self._historical("live-001", "grounding/tool_result")
        reviewed = evaluate_record_v2(self.cases[record.case_id], result, record, purpose="historical_diagnostic_replay")
        self.assertTrue(reviewed.decision.hard_invariants["authorization"])
        self.assertFalse(reviewed.decision.hard_invariants["tool_source_policy"])
        self.assertTrue(reviewed.decision.hard_invariants["result_grounding"])
        self.assertEqual(reviewed.verdict, "FAIL")

    def test_numeric_grounding_ignores_identifiers_but_rejects_wrong_sales_amount(self):
        record, result = self._historical("live-003", "telemetry/token_cost")
        case = self.cases[record.case_id]
        diagnostics = grounding_diagnostics(case, result)
        self.assertEqual(diagnostics.unsupported_business_claims, ())
        categories = {item.category for item in diagnostics.ignored_numeric_context}
        self.assertTrue({"period", "uuid", "version", "latency"} <= categories)
        wrong = result.model_copy(update={
            "final_response": "Q1 2026 total sales: $999.00; orders: 4; AOV: $100.00. Call ID 12345678-1234-1234-1234-123456789012, tool v1.0.0, latency 0 ms."
        })
        wrong_diagnostics = grounding_diagnostics(case, wrong)
        unsupported = {(item.category, item.value) for item in wrong_diagnostics.unsupported_business_claims}
        self.assertIn(("total_sales", "999"), unsupported)
        self.assertNotIn(("order_count", "4"), unsupported)
        self.assertNotIn(("average_order_value", "1E+2"), unsupported)

    def test_historical_replay_is_nine_records_and_preserves_original_verdicts(self):
        before = {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for run_id in ("live-001", "live-002", "live-003")
            for path in (EVAL / run_id).glob("*.json")
        }
        replay = build_replay_payload()
        after = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in before}
        self.assertEqual(before, after)
        self.assertEqual(replay["summary"], {
            "records": 9, "original_v1_pass": 0, "original_v1_fail": 9,
            "diagnostic_v2_pass": 6, "diagnostic_v2_fail": 3,
        })
        self.assertTrue(all(item["original_v1"]["verdict"] == "FAIL" for item in replay["entries"]))

    def test_case_threshold_is_enforced_independently_from_round_average(self):
        case = self.cases["selection/no_tool_answer"]
        scores = {name: 2 for name in case["quality_dimensions"]}
        scores["clarity_concision"] = 1
        reviewed = self._synthetic_review(case, "case-threshold", scores=scores)
        self.assertAlmostEqual(reviewed.normalized_quality, 5 / 6)
        self.assertFalse(reviewed.quality_pass)
        self.assertEqual(reviewed.verdict, "FAIL")

        with tempfile.TemporaryDirectory() as temporary, patch.object(gate_module, "PROJECT_ROOT", Path(temporary)):
            prerequisites = self._prerequisites(Path(temporary), Path(temporary))
            runs = [self._group(f"gate-{index}", "live_model") for index in range(1, 4)]
            target = next(item for item in runs[0] if item.record.case_id == case["id"])
            decision = target.decision.model_copy(update={
                "quality": {name: QualityScore(score=scores[name], rationale="Threshold test.") for name in scores}
            })
            runs[0][runs[0].index(target)] = review_record_v2(target.record, decision)
            gate = evaluate_release_gate_v2(
                runs, control_cases=self._group("controls", "deterministic_control"),
                prerequisites=prerequisites,
            )
            self.assertGreater(gate.run_results[0].normalized_quality, 0.85)
            self.assertIn("per_case_quality_below_threshold", gate.run_results[0].blockers)
            self.assertEqual(gate.verdict, "FAIL")

    def test_gate_uses_hash_bound_prerequisites_instead_of_fixed_booleans(self):
        signature = inspect.signature(evaluate_release_gate_v2)
        self.assertNotIn("offline_tests_passed", signature.parameters)
        self.assertNotIn("database_tests_passed", signature.parameters)
        with tempfile.TemporaryDirectory() as temporary, patch.object(gate_module, "PROJECT_ROOT", Path(temporary)):
            evidence = self._prerequisites(Path(temporary), Path(temporary))
            valid, blockers = verify_prerequisites(evidence)
            self.assertTrue(valid)
            self.assertEqual(blockers, ())
            values = evidence.model_dump(mode="json")
            values.pop("evidence_sha256")
            values["checks"][0]["details"]["tests_run"] = 99
            misleading = build_prerequisite_evidence(values["checks"])
            valid, blockers = verify_prerequisites(misleading)
            self.assertFalse(valid)
            self.assertIn("prerequisite_artifact_content_mismatch:offline_tests", blockers)
            artifact = Path(temporary) / "offline_tests.txt"
            artifact.write_text("tampered")
            valid, blockers = verify_prerequisites(evidence)
            self.assertFalse(valid)
            self.assertIn("prerequisite_artifact_hash_mismatch:offline_tests", blockers)

    def test_v2_control_and_replay_artifacts_are_complete_and_hash_bound(self):
        controls = json.loads((V2 / "deterministic-controls-v2-001" / "reviewed.json").read_text())
        self.assertEqual((controls["case_count"], controls["pass_count"], controls["fail_count"]), (7, 7, 0))
        raw = json.loads((V2 / "deterministic-controls-v2-001" / "raw.json").read_text())
        self.assertEqual({item["suite_sha256"] for item in raw["records"]}, {suite_sha256_v2()})
        replay = json.loads((V2 / "historical-replay-v2.json").read_text())
        expected = replay.pop("replay_evidence_sha256")
        from eval.tools.suite import canonical_sha256
        self.assertEqual(canonical_sha256(replay), expected)

    def test_gate_rejects_legacy_historical_replay_as_release_evidence(self):
        record, result = self._historical("live-001", "selection/no_tool_answer")
        reviewed = evaluate_record_v2(self.cases[record.case_id], result, record, purpose="historical_diagnostic_replay")
        self.assertFalse(reviewed.release_eligible)


if __name__ == "__main__":
    unittest.main()
