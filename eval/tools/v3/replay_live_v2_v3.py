"""Read-only replay of all 27 evaluator-v2 live answers under evaluator v3."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from backend.tools.orchestration import ToolOrchestrationResult
from eval.tools.suite import ToolEvalRecord, canonical_sha256
from eval.tools.v3.evaluator_v3 import evaluate_record_v3
from eval.tools.v3.suite_v3 import load_cases_v3, suite_sha256_v3

ROOT = Path(__file__).resolve().parent
V2_ROOT = ROOT.parent / "v2"
RUN_IDS = ("live-v2-001", "live-v2-002", "live-v2-003")
DEFAULT_OUTPUT = ROOT / "historical-live-v2-replay-v3.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_replay_payload() -> dict[str, Any]:
    cases = {case["id"]: case for case in load_cases_v3()["cases"]}
    entries: list[dict[str, Any]] = []
    source_hashes: dict[str, str] = {}
    for run_id in RUN_IDS:
        raw_path = V2_ROOT / run_id / "raw.json"
        reviewed_path = V2_ROOT / run_id / "reviewed.json"
        decisions_path = V2_ROOT / run_id / "decisions.json"
        for path in (raw_path, reviewed_path, decisions_path):
            source_hashes[str(path.relative_to(ROOT.parents[2]))] = _sha(path)
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        original = {
            item["record"]["case_id"]: item
            for item in json.loads(reviewed_path.read_text(encoding="utf-8"))["cases"]
        }
        for item in raw["records"]:
            record = ToolEvalRecord.model_validate(item, strict=True)
            result = ToolOrchestrationResult.model_validate(
                record.result["orchestration"], strict=True
            )
            reviewed_v3 = evaluate_record_v3(
                cases[record.case_id], result, record,
                purpose="historical_diagnostic_replay",
            )
            prior = original[record.case_id]
            entries.append({
                "run_id": run_id,
                "case_id": record.case_id,
                "evidence_sha256": record.evidence_sha256,
                "source_reference": str(raw_path.relative_to(ROOT.parents[2])),
                "v2": {
                    "verdict": prior["verdict"],
                    "hard_pass": prior["hard_pass"],
                    "core_blocker": prior["decision"]["core_blocker"],
                    "completion_pass": prior["decision"]["diagnostics"]["completion_pass"],
                    "business_claims": prior["decision"]["diagnostics"]["grounding"]["business_claims"],
                    "unsupported_business_claims": prior["decision"]["diagnostics"]["grounding"]["unsupported_business_claims"],
                },
                "v3": {
                    "verdict": reviewed_v3.verdict,
                    "hard_pass": reviewed_v3.hard_pass,
                    "core_blocker": reviewed_v3.decision.core_blocker,
                    "completion_pass": reviewed_v3.decision.diagnostics.completion_pass,
                    "business_claims": [
                        claim.model_dump(mode="json")
                        for claim in reviewed_v3.decision.diagnostics.grounding.business_claims
                    ],
                    "unsupported_business_claims": [
                        claim.model_dump(mode="json")
                        for claim in reviewed_v3.decision.diagnostics.grounding.unsupported_business_claims
                    ],
                    "release_eligible": reviewed_v3.release_eligible,
                },
                "difference": {
                    "verdict_changed": prior["verdict"] != reviewed_v3.verdict,
                    "completion_changed": prior["decision"]["diagnostics"]["completion_pass"]
                    != reviewed_v3.decision.diagnostics.completion_pass,
                },
            })
    changed = [entry for entry in entries if entry["difference"]["verdict_changed"]]
    payload = {
        "replay_version": "tool-calling-live-v2-read-only-replay-v3",
        "purpose": "diagnostic_only_not_release_evidence",
        "source_suite_sha256": next(
            item["source_suite_sha256"]
            for run_id in RUN_IDS
            for item in json.loads((V2_ROOT / run_id / "decisions.json").read_text())["decisions"]
        ),
        "evaluator_suite_sha256": suite_sha256_v3(),
        "source_files_sha256": source_hashes,
        "summary": {
            "records": len(entries),
            "v2_pass": sum(entry["v2"]["verdict"] == "PASS" for entry in entries),
            "v2_fail": sum(entry["v2"]["verdict"] == "FAIL" for entry in entries),
            "diagnostic_v3_pass": sum(entry["v3"]["verdict"] == "PASS" for entry in entries),
            "diagnostic_v3_fail": sum(entry["v3"]["verdict"] == "FAIL" for entry in entries),
            "verdict_changes": len(changed),
            "changed_case_ids": sorted({entry["case_id"] for entry in changed}),
            "paid_provider_calls": 0,
        },
        "entries": entries,
    }
    payload["replay_evidence_sha256"] = canonical_sha256(payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = build_replay_payload()
    if args.write:
        if args.output.exists():
            raise FileExistsError(f"refusing to overwrite historical replay: {args.output}")
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(payload["summary"], sort_keys=True))


if __name__ == "__main__":
    main()
