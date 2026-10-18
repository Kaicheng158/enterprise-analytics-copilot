"""Read-only diagnostic replay of the nine repeated v1 failures under evaluator v2."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from backend.tools.orchestration import ToolOrchestrationResult
from eval.tools.suite import ToolEvalRecord, canonical_sha256
from eval.tools.v2.evaluator_v2 import evaluate_record_v2
from eval.tools.v2.release_gate_v2 import gate_sha256_v2
from eval.tools.v2.suite_v2 import load_cases_v2, suite_sha256_v2

ROOT = Path(__file__).resolve().parent
V1_ROOT = ROOT.parent
FAILURE_IDS = (
    "selection/no_tool_answer",
    "grounding/tool_result",
    "telemetry/token_cost",
)
RUN_IDS = ("live-001", "live-002", "live-003")
DEFAULT_OUTPUT = ROOT / "historical-replay-v2.json"


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_replay_payload() -> dict[str, Any]:
    cases = {case["id"]: case for case in load_cases_v2()["cases"]}
    entries = []
    source_files: dict[str, str] = {}
    for run_id in RUN_IDS:
        raw_path = V1_ROOT / run_id / "raw.json"
        decision_path = V1_ROOT / run_id / "decisions.json"
        reviewed_path = V1_ROOT / run_id / "reviewed.json"
        for path in (raw_path, decision_path, reviewed_path):
            source_files[str(path.relative_to(V1_ROOT.parents[1]))] = _file_sha(path)
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        original_decisions = {
            item["case_id"]: item
            for item in json.loads(decision_path.read_text(encoding="utf-8"))["decisions"]
        }
        original_reviews = {
            item["case_id"]: item
            for item in json.loads(reviewed_path.read_text(encoding="utf-8"))["cases"]
        }
        records = {item["case_id"]: item for item in raw["records"]}
        for case_id in FAILURE_IDS:
            record = ToolEvalRecord.model_validate(records[case_id], strict=True)
            orchestration = ToolOrchestrationResult.model_validate(
                record.result["orchestration"], strict=True
            )
            reviewed_v2 = evaluate_record_v2(
                cases[case_id], orchestration, record,
                purpose="historical_diagnostic_replay",
            )
            original = original_reviews[case_id]
            entries.append({
                "run_id": run_id,
                "case_id": case_id,
                "evidence_sha256": record.evidence_sha256,
                "original_v1": {
                    "verdict": original["semantic"]["verdict"],
                    "hard_invariants": original["hard_invariants"],
                    "core_blocker": original["semantic"]["core_blocker"],
                    "quality_scores": original["semantic"]["quality_scores"],
                    "unsupported_numeric_claims": original["semantic"]["unsupported_numeric_claims"],
                    "decision_sha256": canonical_sha256(original_decisions[case_id]),
                },
                "diagnostic_v2": reviewed_v2.model_dump(mode="json"),
                "difference": {
                    "verdict_changed": original["semantic"]["verdict"] != reviewed_v2.verdict,
                    "authorization_v1": original["hard_invariants"].get("authorization"),
                    "authorization_v2": reviewed_v2.decision.hard_invariants.get("authorization"),
                    "tool_source_policy_v2": reviewed_v2.decision.hard_invariants.get("tool_source_policy"),
                    "result_grounding_v1": original["hard_invariants"].get("result_grounding"),
                    "result_grounding_v2": reviewed_v2.decision.hard_invariants.get("result_grounding"),
                },
                "source_references": {
                    "raw": str(raw_path.relative_to(V1_ROOT.parents[1])),
                    "decision": str(decision_path.relative_to(V1_ROOT.parents[1])),
                    "reviewed": str(reviewed_path.relative_to(V1_ROOT.parents[1])),
                },
            })
    payload = {
        "replay_version": "tool-calling-historical-diagnostic-replay-v2",
        "purpose": "Read-only diagnosis; not release evidence and not a replacement for v1 verdicts.",
        "paid_api_calls": 0,
        "source_v1_suite_sha256": "4f0c8dcfe75dae98d06deac25fff6a2ce1cb743a5f3d5f7d0bd1f2553ad20958",
        "evaluator_v2_suite_sha256": suite_sha256_v2(),
        "evaluator_v2_gate_sha256": gate_sha256_v2(),
        "source_file_sha256": source_files,
        "entries": entries,
        "summary": {
            "records": len(entries),
            "original_v1_pass": sum(item["original_v1"]["verdict"] == "PASS" for item in entries),
            "original_v1_fail": sum(item["original_v1"]["verdict"] == "FAIL" for item in entries),
            "diagnostic_v2_pass": sum(item["diagnostic_v2"]["verdict"] == "PASS" for item in entries),
            "diagnostic_v2_fail": sum(item["diagnostic_v2"]["verdict"] == "FAIL" for item in entries),
        },
    }
    payload["replay_evidence_sha256"] = canonical_sha256(payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite replay evidence: {args.output}")
    payload = build_replay_payload()
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload["summary"], indent=2))


if __name__ == "__main__":
    main()
