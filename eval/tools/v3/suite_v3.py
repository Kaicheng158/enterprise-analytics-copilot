"""Strict contracts for the independently versioned Tool Calling evaluator v3."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from backend.tools.models import StrictModel
from eval.tools.suite import QualityScore, ToolEvalRecord, canonical_sha256

ROOT = Path(__file__).resolve().parent
POLICY_PATH = ROOT / "policy-v3.json"
CASES_PATH = ROOT / "cases-v3.json"
FREEZE_PATH = ROOT / "freeze-v3.json"


def load_policy_v3() -> dict[str, Any]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def load_cases_v3() -> dict[str, Any]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def suite_sha256_v3() -> str:
    return canonical_sha256({"policy": load_policy_v3(), "suite": load_cases_v3()})


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_suite_v3() -> tuple[dict[str, Any], dict[str, Any]]:
    policy = load_policy_v3()
    suite = load_cases_v3()
    if policy["policy_version"] != "tool-calling-evaluation-policy-v3":
        raise ValueError("unexpected v3 policy version")
    if suite["suite_version"] != "tool-calling-suite-v3":
        raise ValueError("unexpected v3 suite version")
    hard_names = set(policy["hard_invariants"])
    quality_names = set(policy["quality_dimensions"])
    cases = suite.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("v3 suite must contain cases")
    ids: set[str] = set()
    required = {
        "id", "category", "user_message", "principal", "expected_tool",
        "expected_behavior", "failure_condition", "hard_invariants",
        "quality_dimensions", "execution_track", "tool_policy",
        "expected_outcomes", "completion_rule",
    }
    for case in cases:
        if set(case) != required:
            raise ValueError(f"invalid v3 case fields: {case.get('id')}")
        if case["id"] in ids:
            raise ValueError(f"duplicate v3 case id: {case['id']}")
        ids.add(case["id"])
        if not set(case["hard_invariants"]) <= hard_names:
            raise ValueError(f"unknown v3 hard invariant: {case['id']}")
        if not set(case["quality_dimensions"]) <= quality_names:
            raise ValueError(f"unknown v3 quality dimension: {case['id']}")
        tool_policy = case["tool_policy"]
        if set(tool_policy) != {"mode", "allowed_tools", "source_kind", "rationale"}:
            raise ValueError(f"invalid tool policy: {case['id']}")
        if tool_policy["mode"] not in {"required", "forbidden", "not_applicable"}:
            raise ValueError(f"invalid tool policy mode: {case['id']}")
        if tool_policy["mode"] == "required" and not tool_policy["allowed_tools"]:
            raise ValueError(f"required tool policy has no allowed tool: {case['id']}")
        if tool_policy["mode"] == "forbidden" and tool_policy["allowed_tools"]:
            raise ValueError(f"forbidden tool policy exposes allowed tools: {case['id']}")
        if tool_policy["mode"] == "not_applicable" and "tool_source_policy" in case["hard_invariants"]:
            raise ValueError(f"N/A source policy marked hard: {case['id']}")
        if tool_policy["mode"] != "not_applicable" and "tool_source_policy" not in case["hard_invariants"]:
            raise ValueError(f"applicable source policy missing hard check: {case['id']}")
    return policy, suite


class NumericClaimV3(StrictModel):
    category: Literal[
        "total_sales", "average_order_value", "order_count",
        "unclassified_currency_amount",
    ]
    value: str
    text: str


class IgnoredNumericContextV3(StrictModel):
    category: Literal["period", "uuid", "version", "latency", "token_usage", "telemetry_cost"]
    text: str


class GroundingDiagnosticsV3(StrictModel):
    business_claims: tuple[NumericClaimV3, ...]
    ignored_numeric_context: tuple[IgnoredNumericContextV3, ...]
    unsupported_business_claims: tuple[NumericClaimV3, ...]


class EvaluationDiagnosticsV3(StrictModel):
    authorization_pass: bool
    tool_source_policy_pass: bool | None
    aov_definition_pass: bool | None
    grounding: GroundingDiagnosticsV3
    completion_pass: bool
    classification_notes: tuple[str, ...]


class ToolEvalDecisionV3(StrictModel):
    decision_version: Literal["tool-eval-decision-v3"] = "tool-eval-decision-v3"
    purpose: Literal["release", "historical_diagnostic_replay"]
    case_id: str
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_suite_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluator_suite_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    hard_invariants: dict[str, bool]
    core_blocker: bool
    quality: dict[str, QualityScore]
    repeated_severe_quality_issue: bool = False
    reviewer: Literal["tool-live-review-v3"] = "tool-live-review-v3"
    rationale: str = Field(min_length=1)
    diagnostics: EvaluationDiagnosticsV3


class ReviewedToolCaseV3(StrictModel):
    record: ToolEvalRecord
    decision: ToolEvalDecisionV3
    hard_pass: bool
    normalized_quality: float | None
    quality_pass: bool
    release_eligible: bool
    verdict: Literal["PASS", "FAIL"]


def review_record_v3(record: ToolEvalRecord, decision: ToolEvalDecisionV3) -> ReviewedToolCaseV3:
    policy, suite = validate_suite_v3()
    cases = {item["id"]: item for item in suite["cases"]}
    case = cases.get(record.case_id)
    if case is None or decision.case_id != record.case_id:
        raise ValueError("unknown or mismatched v3 case")
    if decision.evidence_sha256 != record.evidence_sha256:
        raise ValueError("v3 review is not bound to exact evidence")
    if decision.source_suite_sha256 != record.suite_sha256:
        raise ValueError("v3 decision source suite mismatch")
    if decision.evaluator_suite_sha256 != suite_sha256_v3():
        raise ValueError("v3 evaluator suite identity mismatch")
    if set(decision.hard_invariants) != set(case["hard_invariants"]):
        raise ValueError("v3 hard-invariant applicability mismatch")
    if set(decision.quality) != set(case["quality_dimensions"]):
        raise ValueError("v3 quality applicability mismatch")
    hard_pass = all(decision.hard_invariants.values())
    scores = [item.score for item in decision.quality.values() if item.score is not None]
    normalized = sum(scores) / (2 * len(scores)) if scores else None
    threshold = policy["release_rules"]["minimum_case_normalized_quality"]
    quality_pass = normalized is not None and normalized >= threshold and all(score > 0 for score in scores)
    release_eligible = (
        decision.purpose == "release"
        and record.suite_sha256 == suite_sha256_v3()
    )
    verdict = "PASS" if hard_pass and not decision.core_blocker and quality_pass else "FAIL"
    return ReviewedToolCaseV3(
        record=record,
        decision=decision,
        hard_pass=hard_pass,
        normalized_quality=normalized,
        quality_pass=quality_pass,
        release_eligible=release_eligible,
        verdict=verdict,
    )


def verify_freeze_v3() -> dict[str, Any]:
    freeze = json.loads(FREEZE_PATH.read_text(encoding="utf-8"))
    if freeze["suite_sha256"] != suite_sha256_v3():
        raise ValueError("v3 suite freeze mismatch")
    expected_files = freeze["file_sha256"]
    for relative, expected in expected_files.items():
        path = ROOT.parents[2] / relative
        if file_sha256(path) != expected:
            raise ValueError(f"v3 frozen file changed: {relative}")
    return freeze
