"""Run fresh deterministic v2 controls without network or paid providers."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from backend.tools.orchestration import ToolOrchestrationResult
from eval.tools.run_controls import RUNNERS
from eval.tools.suite import build_record, canonical_sha256
from eval.tools.v2.evaluator_v2 import evaluate_record_v2
from eval.tools.v2.release_gate_v2 import gate_sha256_v2
from eval.tools.v2.suite_v2 import load_cases_v2, suite_sha256_v2, verify_freeze_v2

ROOT = Path(__file__).resolve().parent
RUN_ID = "deterministic-controls-v2-001"
OUTPUT = ROOT / RUN_ID


def run():
    if OUTPUT.exists():
        raise FileExistsError(f"refusing to overwrite v2 control evidence: {OUTPUT}")
    freeze_before = verify_freeze_v2()
    cases = [case for case in load_cases_v2()["cases"] if case["execution_track"] == "deterministic_control"]
    records = []
    reviews = []
    observations = []
    for case in cases:
        result, source_record, observation = RUNNERS[case["id"]](case)
        record = build_record(
            run_id=RUN_ID,
            case_id=case["id"],
            mode=source_record.mode,
            suite_sha256=suite_sha256_v2(),
            prompt_version=source_record.prompt_version,
            prompt_sha256=source_record.prompt_sha256,
            user_message=case["user_message"],
            result=source_record.result,
            telemetry=source_record.telemetry,
        )
        parsed = ToolOrchestrationResult.model_validate(record.result["orchestration"], strict=True)
        reviewed = evaluate_record_v2(case, parsed, record, purpose="release")
        records.append(record)
        reviews.append(reviewed)
        observations.append({
            "case_id": case["id"],
            "source_observation": observation,
            "source_v1_record_sha256": source_record.evidence_sha256,
        })
    if verify_freeze_v2() != freeze_before:
        raise RuntimeError("v2 freeze changed during deterministic controls")
    raw = {
        "run_id": RUN_ID,
        "mode": "deterministic_no_network",
        "suite_sha256": suite_sha256_v2(),
        "gate_sha256": gate_sha256_v2(),
        "paid_api_calls": 0,
        "records": [item.model_dump(mode="json") for item in records],
        "observations": observations,
    }
    decisions = {
        "run_id": RUN_ID,
        "decisions": [item.decision.model_dump(mode="json") for item in reviews],
    }
    reviewed_payload = {
        "run_id": RUN_ID,
        "case_count": len(reviews),
        "pass_count": sum(item.verdict == "PASS" for item in reviews),
        "fail_count": sum(item.verdict == "FAIL" for item in reviews),
        "normalized_quality": sum(item.normalized_quality or 0 for item in reviews) / len(reviews),
        "cases": [item.model_dump(mode="json") for item in reviews],
        "reviewed_evidence_sha256": canonical_sha256([item.model_dump(mode="json") for item in reviews]),
    }
    with tempfile.TemporaryDirectory(prefix=f".{RUN_ID}-", dir=ROOT) as temp_name:
        temp = Path(temp_name)
        for name, payload in {"raw.json": raw, "decisions.json": decisions, "reviewed.json": reviewed_payload}.items():
            (temp / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temp.rename(OUTPUT)
    print(json.dumps({"run_id": RUN_ID, "pass": reviewed_payload["pass_count"], "fail": reviewed_payload["fail_count"]}, indent=2))
    return reviewed_payload


if __name__ == "__main__":
    run()
