"""Run fresh deterministic v3 controls without network or paid providers."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from backend.tools.orchestration import ToolOrchestrationResult
from eval.tools.run_controls import RUNNERS
from eval.tools.suite import build_record, canonical_sha256, verify_freeze
from eval.tools.v2.suite_v2 import verify_freeze_v2
from eval.tools.v3.evaluator_v3 import evaluate_record_v3
from eval.tools.v3.release_gate_v3 import gate_sha256_v3
from eval.tools.v3.suite_v3 import load_cases_v3, suite_sha256_v3, verify_freeze_v3

ROOT = Path(__file__).resolve().parent
RUN_ID = "deterministic-controls-v3-001"
OUTPUT = ROOT / RUN_ID


def run():
    if OUTPUT.exists():
        raise FileExistsError(f"refusing to overwrite v3 control evidence: {OUTPUT}")
    freezes_before = (verify_freeze(), verify_freeze_v2(), verify_freeze_v3())
    cases = [case for case in load_cases_v3()["cases"] if case["execution_track"] == "deterministic_control"]
    records = []
    reviews = []
    observations = []
    for case in cases:
        result, source_record, observation = RUNNERS[case["id"]](case)
        telemetry = source_record.telemetry
        if (
            case["id"] == "errors/provider_timeout"
            and source_record.mode == "mock_api"
            and telemetry.total_tokens == 0
            and telemetry.estimated_cost is None
        ):
            # This deterministic control performs no network request and consumes
            # no provider tokens. Record its known local cost as zero so the
            # frozen telemetry-integrity rule remains strict for live evidence.
            telemetry = telemetry.model_copy(update={
                "estimated_cost": "0",
                "cost_complete": True,
            })
            observation = {
                **observation,
                "deterministic_cost_classification": "no_network_zero_cost",
            }
        record = build_record(
            run_id=RUN_ID,
            case_id=case["id"],
            mode=source_record.mode,
            suite_sha256=suite_sha256_v3(),
            prompt_version=source_record.prompt_version,
            prompt_sha256=source_record.prompt_sha256,
            user_message=case["user_message"],
            result=source_record.result,
            telemetry=telemetry,
        )
        parsed = ToolOrchestrationResult.model_validate(record.result["orchestration"], strict=True)
        reviewed = evaluate_record_v3(case, parsed, record, purpose="release")
        records.append(record)
        reviews.append(reviewed)
        observations.append({
            "case_id": case["id"],
            "source_observation": observation,
            "source_v1_record_sha256": source_record.evidence_sha256,
        })
    if (verify_freeze(), verify_freeze_v2(), verify_freeze_v3()) != freezes_before:
        raise RuntimeError("a frozen v1/v2/v3 identity changed during deterministic controls")
    raw = {
        "run_id": RUN_ID,
        "mode": "deterministic_no_network",
        "suite_sha256": suite_sha256_v3(),
        "gate_sha256": gate_sha256_v3(),
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
