"""Pre-registered release gate v3 with hash-bound prerequisite evidence."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from backend.tools.models import StrictModel
from eval.tools.suite import canonical_sha256
from eval.tools.v3.suite_v3 import ReviewedToolCaseV3, suite_sha256_v3

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[2]
GATE_PATH = ROOT / "release-gate-v3.json"
PREREQUISITE_PATH = ROOT / "prerequisite-evidence-v3.json"


def load_gate_v3() -> dict[str, Any]:
    return json.loads(GATE_PATH.read_text(encoding="utf-8"))


def gate_sha256_v3() -> str:
    return canonical_sha256(load_gate_v3())


class PrerequisiteCheckV3(StrictModel):
    name: Literal["offline_tests", "database_tests", "feature_flag_default_off", "public_tool_api_absent"]
    status: Literal["PASS", "FAIL"]
    artifact_path: str = Field(min_length=1)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    details: dict[str, Any]


class PrerequisiteEvidenceV3(StrictModel):
    evidence_version: Literal["tool-gate-prerequisites-v3"] = "tool-gate-prerequisites-v3"
    suite_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    gate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    checks: tuple[PrerequisiteCheckV3, ...]
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def hash_matches(self):
        payload = self.model_dump(exclude={"evidence_sha256"}, mode="json")
        if self.evidence_sha256 != canonical_sha256(payload):
            raise ValueError("prerequisite evidence hash mismatch")
        return self


def build_prerequisite_evidence(checks: list[dict[str, Any]]) -> PrerequisiteEvidenceV3:
    payload = {
        "evidence_version": "tool-gate-prerequisites-v3",
        "suite_sha256": suite_sha256_v3(),
        "gate_sha256": gate_sha256_v3(),
        "checks": tuple(checks),
    }
    return PrerequisiteEvidenceV3.model_validate(
        {**payload, "evidence_sha256": canonical_sha256(payload)}, strict=True
    )


def load_prerequisite_evidence() -> PrerequisiteEvidenceV3:
    return PrerequisiteEvidenceV3.model_validate_json(
        PREREQUISITE_PATH.read_text(encoding="utf-8"), strict=True
    )


def verify_prerequisites(evidence: PrerequisiteEvidenceV3) -> tuple[bool, tuple[str, ...]]:
    gate = load_gate_v3()
    blockers: list[str] = []
    if evidence.suite_sha256 != suite_sha256_v3():
        blockers.append("prerequisite_suite_identity_mismatch")
    if evidence.gate_sha256 != gate_sha256_v3():
        blockers.append("prerequisite_gate_identity_mismatch")
    required = set(gate["required_prerequisite_checks"])
    names = [check.name for check in evidence.checks]
    if set(names) != required or len(names) != len(required):
        blockers.append("prerequisite_set_incomplete_or_duplicate")
    for check in evidence.checks:
        if check.status != "PASS":
            blockers.append(f"prerequisite_failed:{check.name}")
        details = check.details
        if check.name in {"offline_tests", "database_tests"}:
            if not (
                details.get("exit_code") == 0
                and isinstance(details.get("tests_run"), int)
                and details.get("tests_run", 0) > 0
                and details.get("failures") == 0
                and details.get("errors") == 0
            ):
                blockers.append(f"prerequisite_details_invalid:{check.name}")
        elif check.name == "feature_flag_default_off":
            if details.get("setting") != "TOOL_CALLING_ENABLED" or details.get("default_value") is not False:
                blockers.append("prerequisite_details_invalid:feature_flag_default_off")
        elif check.name == "public_tool_api_absent":
            if details.get("public_tool_routes") != []:
                blockers.append("prerequisite_details_invalid:public_tool_api_absent")
        path = (PROJECT_ROOT / check.artifact_path).resolve()
        try:
            path.relative_to(PROJECT_ROOT.resolve())
        except ValueError:
            blockers.append(f"prerequisite_path_escape:{check.name}")
            continue
        if not path.is_file():
            blockers.append(f"prerequisite_artifact_missing:{check.name}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != check.artifact_sha256:
            blockers.append(f"prerequisite_artifact_hash_mismatch:{check.name}")
            continue
        if check.name in {"offline_tests", "database_tests"}:
            text = path.read_text(encoding="utf-8")
            match = re.search(r"Ran (\d+) tests? in ", text)
            content_ok = bool(match and re.search(r"^OK$", text, re.M))
            if not content_ok or int(match.group(1)) != details["tests_run"]:
                blockers.append(f"prerequisite_artifact_content_mismatch:{check.name}")
        elif check.name in {"feature_flag_default_off", "public_tool_api_absent"}:
            try:
                runtime = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeError):
                blockers.append(f"prerequisite_artifact_content_mismatch:{check.name}")
                continue
            if check.name == "feature_flag_default_off" and not (
                runtime.get("feature_flag", {}).get("default_value") is False
                and runtime.get("feature_flag", {}).get("effective_enabled") is False
            ):
                blockers.append("prerequisite_artifact_content_mismatch:feature_flag_default_off")
            if check.name == "public_tool_api_absent" and runtime.get("route_surface", {}).get("public_tool_routes") != []:
                blockers.append("prerequisite_artifact_content_mismatch:public_tool_api_absent")
    return not blockers, tuple(dict.fromkeys(blockers))


class RunGateResultV3(StrictModel):
    run_id: str
    hard_invariants_pass: bool
    core_blockers: int = Field(ge=0)
    below_case_threshold: tuple[str, ...]
    case_verdict_failures: tuple[str, ...]
    normalized_quality: float | None
    repeated_severe_quality_issue: bool
    verdict: Literal["PASS", "FAIL"]
    blockers: tuple[str, ...]


class ToolReleaseGateResultV3(StrictModel):
    gate_version: str
    suite_sha256: str
    prerequisite_evidence_sha256: str | None
    prerequisite_pass: bool
    control_result: RunGateResultV3 | None
    live_runs_received: int = Field(ge=0)
    run_results: tuple[RunGateResultV3, ...]
    repeated_cross_run_failures: tuple[str, ...]
    verdict: Literal["PASS", "FAIL", "NOT_RUN"]
    blockers: tuple[str, ...]


def _score_group(
    items: list[ReviewedToolCaseV3], gate: dict[str, Any], *,
    expected_case_ids: set[str], live: bool,
) -> RunGateResultV3:
    run_ids = {item.record.run_id for item in items}
    if len(run_ids) != 1:
        raise ValueError("a v3 gate group must have one run_id")
    actual = [item.record.case_id for item in items]
    blockers: list[str] = []
    if set(actual) != expected_case_ids or len(actual) != len(expected_case_ids):
        blockers.append("incomplete_or_duplicate_case_set")
    if any(not item.release_eligible for item in items):
        blockers.append("non_release_or_legacy_evidence")
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
        config = gate["fixed_live_config"]
        checked = (
            "temperature", "thinking", "max_output_tokens", "max_provider_interactions",
            "max_tool_executions", "automatic_retries",
        )
        for item in items:
            telemetry = item.record.telemetry
            if telemetry.provider != config["provider"] or telemetry.model != config["model"]:
                blockers.append("provider_or_model_mismatch")
                break
            if any(telemetry.generation_config.get(key) != config[key] for key in checked):
                blockers.append("generation_config_mismatch")
                break
    hard_pass = all(item.hard_pass for item in items)
    if not hard_pass:
        blockers.append("hard_invariant_failure")
    core = sum(item.decision.core_blocker for item in items)
    if core:
        blockers.append("core_task_blocker")
    below = tuple(item.record.case_id for item in items if not item.quality_pass)
    if below:
        blockers.append("per_case_quality_below_threshold")
    case_failures = tuple(item.record.case_id for item in items if item.verdict != "PASS")
    if case_failures:
        blockers.append("case_verdict_failure")
    scores = [
        score.score for item in items for score in item.decision.quality.values()
        if score.score is not None
    ]
    normalized = sum(scores) / (2 * len(scores)) if scores else None
    if normalized is None or normalized < gate["minimum_round_normalized_quality"]:
        blockers.append("round_quality_below_threshold")
    if any(score == 0 for score in scores):
        blockers.append("zero_quality_dimension")
    severe = any(item.decision.repeated_severe_quality_issue for item in items)
    if severe:
        blockers.append("repeated_severe_quality_issue")
    return RunGateResultV3(
        run_id=next(iter(run_ids)), hard_invariants_pass=hard_pass,
        core_blockers=core, below_case_threshold=below,
        case_verdict_failures=case_failures, normalized_quality=normalized,
        repeated_severe_quality_issue=severe,
        verdict="PASS" if not blockers else "FAIL", blockers=tuple(dict.fromkeys(blockers)),
    )


def _cross_run_failures(runs: list[list[ReviewedToolCaseV3]]) -> tuple[str, ...]:
    if len(runs) < 2:
        return ()
    failed_sets = []
    for run in runs:
        failed_sets.append({
            f"{item.record.case_id}:{dimension}"
            for item in run
            for dimension, score in item.decision.quality.items()
            if score.score == 0
        } | {
            f"{item.record.case_id}:core" for item in run if item.decision.core_blocker
        })
    repeated = set.intersection(*failed_sets) if failed_sets else set()
    return tuple(sorted(repeated))


def evaluate_release_gate_v3(
    runs: list[list[ReviewedToolCaseV3]], *,
    control_cases: list[ReviewedToolCaseV3] | None,
    prerequisites: PrerequisiteEvidenceV3,
) -> ToolReleaseGateResultV3:
    gate = load_gate_v3()
    if gate["suite_sha256"] != suite_sha256_v3():
        raise ValueError("v3 release gate suite hash is stale")
    prereq_pass, prereq_blockers = verify_prerequisites(prerequisites)
    blockers = list(prereq_blockers)
    control_result = None
    if control_cases:
        control_result = _score_group(
            control_cases, gate,
            expected_case_ids=set(gate["deterministic_control_case_ids"]), live=False,
        )
        if control_result.verdict != "PASS":
            blockers.append("deterministic_controls_failed")
    else:
        blockers.append("deterministic_control_evidence_missing")
    minimum = gate["minimum_independent_live_runs"]
    if len(runs) < minimum:
        blockers.append("minimum_live_runs_not_met")
    run_results = tuple(
        _score_group(run, gate, expected_case_ids=set(gate["live_model_case_ids"]), live=True)
        for run in runs
    )
    if any(item.verdict != "PASS" for item in run_results):
        blockers.append("one_or_more_live_runs_failed")
    if runs and len({item.run_id for item in run_results}) != len(run_results):
        blockers.append("live_runs_not_independent")
    repeated = _cross_run_failures(runs)
    if repeated:
        blockers.append("repeated_cross_run_quality_failure")
    if not runs:
        verdict: Literal["PASS", "FAIL", "NOT_RUN"] = "NOT_RUN"
    else:
        verdict = "PASS" if not blockers else "FAIL"
    return ToolReleaseGateResultV3(
        gate_version=gate["gate_version"], suite_sha256=gate["suite_sha256"],
        prerequisite_evidence_sha256=prerequisites.evidence_sha256,
        prerequisite_pass=prereq_pass, control_result=control_result,
        live_runs_received=len(runs), run_results=run_results,
        repeated_cross_run_failures=repeated, verdict=verdict,
        blockers=tuple(dict.fromkeys(blockers)),
    )
