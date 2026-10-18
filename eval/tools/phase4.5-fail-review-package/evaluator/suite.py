"""Frozen Tool Calling suite, evidence, and evidence-bound review contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from backend.tools.models import StrictModel

ROOT = Path(__file__).resolve().parent
POLICY_PATH = ROOT / "policy.json"
CASES_PATH = ROOT / "cases.json"
FREEZE_PATH = ROOT / "freeze.json"


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_policy() -> dict[str, Any]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def load_cases() -> dict[str, Any]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def suite_sha256() -> str:
    return canonical_sha256({"policy": load_policy(), "suite": load_cases()})


def validate_suite() -> tuple[dict[str, Any], dict[str, Any]]:
    policy = load_policy()
    suite = load_cases()
    hard_names = set(policy["hard_invariants"])
    quality_names = set(policy["quality_dimensions"])
    cases = suite.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("tool evaluation suite must contain cases")
    ids: set[str] = set()
    for case in cases:
        required = {
            "id", "category", "user_message", "principal", "expected_tool",
            "expected_behavior", "failure_condition", "hard_invariants",
            "quality_dimensions", "execution_track",
        }
        if set(case) != required:
            raise ValueError(f"invalid case fields: {case.get('id')}")
        if case["id"] in ids:
            raise ValueError(f"duplicate case id: {case['id']}")
        ids.add(case["id"])
        if not set(case["hard_invariants"]) <= hard_names:
            raise ValueError(f"unknown hard invariant: {case['id']}")
        if not set(case["quality_dimensions"]) <= quality_names:
            raise ValueError(f"unknown quality dimension: {case['id']}")
        if not case["hard_invariants"]:
            raise ValueError(f"case has no hard invariant: {case['id']}")
        if case["execution_track"] not in {"live_model", "deterministic_control"}:
            raise ValueError(f"invalid execution track: {case['id']}")
    return policy, suite


class ToolEvalTelemetry(StrictModel):
    provider: str
    model: str
    generation_config: dict[str, Any]
    selected_tool: str | None
    arguments_valid: bool | None
    authorization: Literal["allowed", "denied", "not_applicable"]
    execution_status: Literal["success", "error", "not_executed"]
    provider_interactions: int = Field(ge=0, le=2)
    tool_executions: int = Field(ge=0, le=1)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    estimated_cost: str | None
    cost_complete: bool
    retry_count: Literal[0] = 0
    error_code: str | None

    @model_validator(mode="after")
    def totals_and_bounds_are_consistent(self):
        forbidden = {"api_key", "authorization", "headers", "secret", "token_value"}
        stack = [self.generation_config]
        while stack:
            current = stack.pop()
            for key, value in current.items():
                if key.lower() in forbidden:
                    raise ValueError("generation config contains a sensitive field")
                if isinstance(value, dict):
                    stack.append(value)
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("token totals are inconsistent")
        if self.tool_executions > self.provider_interactions:
            raise ValueError("tool executions exceed provider interactions")
        return self


class ToolEvalRecord(StrictModel):
    run_id: str = Field(min_length=1, max_length=128)
    case_id: str
    mode: Literal["offline_fake", "mock_api", "live"]
    suite_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_version: str
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    user_message_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: dict[str, Any]
    telemetry: ToolEvalTelemetry
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def evidence_hash_matches(self):
        payload = self.model_dump(exclude={"evidence_sha256"}, mode="json")
        if self.evidence_sha256 != canonical_sha256(payload):
            raise ValueError("evidence hash mismatch")
        return self


class QualityScore(StrictModel):
    score: int | None = Field(default=None, ge=0, le=2)
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def na_requires_reason(self):
        if self.score is None and not self.rationale.strip():
            raise ValueError("N/A requires rationale")
        return self


class ToolEvalDecision(StrictModel):
    case_id: str
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    hard_invariants: dict[str, bool]
    core_blocker: bool
    quality: dict[str, QualityScore]
    repeated_severe_quality_issue: bool = False
    reviewer: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class ReviewedToolCase(StrictModel):
    record: ToolEvalRecord
    decision: ToolEvalDecision
    hard_pass: bool
    normalized_quality: float | None
    verdict: Literal["PASS", "FAIL"]


def build_record(**values: Any) -> ToolEvalRecord:
    values = dict(values)
    values["user_message_sha256"] = hashlib.sha256(
        values.pop("user_message").encode("utf-8")
    ).hexdigest()
    values["telemetry"] = ToolEvalTelemetry.model_validate(
        values["telemetry"], strict=True
    )
    provisional = ToolEvalRecord.model_construct(
        **values, evidence_sha256="0" * 64
    )
    payload = provisional.model_dump(exclude={"evidence_sha256"}, mode="json")
    return ToolEvalRecord.model_validate(
        {**payload, "evidence_sha256": canonical_sha256(payload)}, strict=True
    )


def review_record(record: ToolEvalRecord, decision: ToolEvalDecision) -> ReviewedToolCase:
    policy, suite = validate_suite()
    cases = {item["id"]: item for item in suite["cases"]}
    case = cases.get(record.case_id)
    if case is None or decision.case_id != record.case_id:
        raise ValueError("unknown or mismatched case")
    if record.suite_sha256 != suite_sha256():
        raise ValueError("record uses an unfrozen suite")
    if decision.evidence_sha256 != record.evidence_sha256:
        raise ValueError("review is not bound to exact evidence")
    if set(decision.hard_invariants) != set(case["hard_invariants"]):
        raise ValueError("hard-invariant applicability does not match case")
    if set(decision.quality) != set(case["quality_dimensions"]):
        raise ValueError("quality applicability does not match case")
    hard_pass = all(decision.hard_invariants.values())
    scores = [item.score for item in decision.quality.values() if item.score is not None]
    normalized = sum(scores) / (2 * len(scores)) if scores else None
    threshold = policy["release_rules"]["minimum_normalized_quality"]
    quality_pass = normalized is not None and normalized >= threshold and all(
        score > 0 for score in scores
    )
    verdict = "PASS" if hard_pass and not decision.core_blocker and quality_pass else "FAIL"
    return ReviewedToolCase(
        record=record,
        decision=decision,
        hard_pass=hard_pass,
        normalized_quality=normalized,
        verdict=verdict,
    )


def verify_freeze() -> dict[str, Any]:
    freeze = json.loads(FREEZE_PATH.read_text(encoding="utf-8"))
    if freeze["suite_sha256"] != suite_sha256():
        raise ValueError("frozen suite hash mismatch")
    if freeze["policy_sha256"] != hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest():
        raise ValueError("frozen policy file hash mismatch")
    if freeze["cases_sha256"] != hashlib.sha256(CASES_PATH.read_bytes()).hexdigest():
        raise ValueError("frozen cases file hash mismatch")
    gate_path = ROOT / "release-gate-v1.json"
    if freeze["release_gate_sha256"] != hashlib.sha256(gate_path.read_bytes()).hexdigest():
        raise ValueError("frozen release gate hash mismatch")
    repository_root = ROOT.parents[1]
    for relative, expected in freeze["implementation_sha256"].items():
        actual = hashlib.sha256((repository_root / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"frozen implementation hash mismatch: {relative}")
    return freeze
