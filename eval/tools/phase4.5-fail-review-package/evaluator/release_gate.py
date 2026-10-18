"""Pre-registered Phase 4.5 Tool Calling release gate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from backend.tools.models import StrictModel
from eval.tools.suite import ReviewedToolCase, canonical_sha256, suite_sha256

ROOT = Path(__file__).resolve().parent
GATE_PATH = ROOT / "release-gate-v1.json"


class RunGateResult(StrictModel):
    run_id: str
    hard_invariants_pass: bool
    core_blockers: int = Field(ge=0)
    normalized_quality: float | None
    repeated_severe_quality_issue: bool
    verdict: Literal["PASS", "FAIL"]
    blockers: tuple[str, ...]


class ToolReleaseGateResult(StrictModel):
    gate_version: str
    suite_sha256: str
    control_result: RunGateResult | None
    live_runs_received: int = Field(ge=0)
    run_results: tuple[RunGateResult, ...]
    verdict: Literal["PASS", "FAIL", "NOT_RUN"]
    blockers: tuple[str, ...]


def load_gate() -> dict:
    return json.loads(GATE_PATH.read_text(encoding="utf-8"))


def gate_sha256() -> str:
    return canonical_sha256(load_gate())


def _score_group(
    items: list[ReviewedToolCase],
    gate: dict,
    *,
    expected_case_ids: set[str],
    live: bool,
) -> RunGateResult:
    run_ids = {item.record.run_id for item in items}
    if len(run_ids) != 1:
        raise ValueError("a gate group must have one run_id")
    actual_cases = {item.record.case_id for item in items}
    blockers: list[str] = []
    if actual_cases != expected_case_ids or len(items) != len(expected_case_ids):
        blockers.append("incomplete_or_duplicate_case_set")
    if live:
        if any(item.record.mode != "live" for item in items):
            blockers.append("non_live_evidence")
    elif any(item.record.mode not in {"offline_fake", "mock_api"} for item in items):
        blockers.append("invalid_control_evidence_mode")
    if any(item.record.suite_sha256 != gate["suite_sha256"] for item in items):
        blockers.append("suite_identity_mismatch")
    if any(item.record.prompt_sha256 != gate["prompt_sha256"] for item in items):
        blockers.append("prompt_identity_mismatch")
    if live:
        expected_config = gate["fixed_live_config"]
        checked = {
            "temperature": expected_config["temperature"],
            "thinking": expected_config["thinking"],
            "max_output_tokens": expected_config["max_output_tokens"],
            "max_provider_interactions": expected_config["max_provider_interactions"],
            "max_tool_executions": expected_config["max_tool_executions"],
            "automatic_retries": expected_config["automatic_retries"],
        }
        for item in items:
            telemetry = item.record.telemetry
            if telemetry.provider != expected_config["provider"] or telemetry.model != expected_config["model"]:
                blockers.append("provider_or_model_mismatch")
                break
            if any(telemetry.generation_config.get(key) != value for key, value in checked.items()):
                blockers.append("generation_config_mismatch")
                break
    hard_pass = all(item.hard_pass for item in items)
    if not hard_pass:
        blockers.append("hard_invariant_failure")
    core = sum(item.decision.core_blocker for item in items)
    if core:
        blockers.append("core_task_blocker")
    scores = [
        score.score
        for item in items
        for score in item.decision.quality.values()
        if score.score is not None
    ]
    normalized = sum(scores) / (2 * len(scores)) if scores else None
    if normalized is None or normalized < gate["minimum_normalized_quality"]:
        blockers.append("semantic_quality_below_threshold")
    if any(score == 0 for score in scores):
        blockers.append("zero_quality_dimension")
    severe = any(item.decision.repeated_severe_quality_issue for item in items)
    if severe:
        blockers.append("repeated_severe_quality_issue")
    return RunGateResult(
        run_id=next(iter(run_ids)),
        hard_invariants_pass=hard_pass,
        core_blockers=core,
        normalized_quality=normalized,
        repeated_severe_quality_issue=severe,
        verdict="PASS" if not blockers else "FAIL",
        blockers=tuple(dict.fromkeys(blockers)),
    )


def evaluate_release_gate(
    runs: list[list[ReviewedToolCase]],
    *,
    control_cases: list[ReviewedToolCase] | None = None,
    offline_tests_passed: bool,
    database_tests_passed: bool,
    feature_flag_default_off: bool,
    public_tool_api_absent: bool,
) -> ToolReleaseGateResult:
    gate = load_gate()
    if gate["suite_sha256"] != suite_sha256():
        raise ValueError("release gate suite hash is stale")
    blockers: list[str] = []
    if not offline_tests_passed:
        blockers.append("offline_tests_failed")
    if not database_tests_passed:
        blockers.append("database_tests_failed")
    if not feature_flag_default_off:
        blockers.append("feature_flag_not_default_off")
    if not public_tool_api_absent:
        blockers.append("public_tool_api_present")
    control_result = None
    if control_cases:
        control_result = _score_group(
            control_cases,
            gate,
            expected_case_ids=set(gate["deterministic_control_case_ids"]),
            live=False,
        )
        if control_result.verdict != "PASS":
            blockers.append("deterministic_controls_failed")
    else:
        blockers.append("deterministic_control_evidence_missing")
    minimum = gate["minimum_independent_live_runs"]
    if len(runs) < minimum:
        blockers.append("minimum_live_runs_not_met")
    run_results = tuple(
        _score_group(
            run,
            gate,
            expected_case_ids=set(gate["live_model_case_ids"]),
            live=True,
        )
        for run in runs
    )
    if any(item.verdict != "PASS" for item in run_results):
        blockers.append("one_or_more_live_runs_failed")
    if runs and len({item.run_id for item in run_results}) != len(run_results):
        blockers.append("live_runs_not_independent")
    if not runs:
        verdict: Literal["PASS", "FAIL", "NOT_RUN"] = "NOT_RUN"
    else:
        verdict = "PASS" if not blockers else "FAIL"
    return ToolReleaseGateResult(
        gate_version=gate["gate_version"],
        suite_sha256=gate["suite_sha256"],
        control_result=control_result,
        live_runs_received=len(runs),
        run_results=run_results,
        verdict=verdict,
        blockers=tuple(dict.fromkeys(blockers)),
    )
