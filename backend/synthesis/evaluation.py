"""Frozen, provider-independent PV2-04e evaluation and release gate."""

from __future__ import annotations

from pydantic import Field, model_validator

from backend.product_facade.models import ProductContract


class SynthesisQualityScores(ProductContract):
    completeness: int = Field(ge=0, le=2)
    relevance: int = Field(ge=0, le=2)
    clarity: int = Field(ge=0, le=2)

    @property
    def normalized(self) -> float:
        return (self.completeness + self.relevance + self.clarity) / 6


class SynthesisEvaluationRecord(ProductContract):
    evaluation_version: str = "trusted-synthesis-eval-v1"
    case_id: str = Field(pattern=r"^[a-z][a-z0-9_/-]{1,79}$")
    schema_valid: bool
    hard_invariants: dict[str, bool]
    core_task_complete: bool
    quality: SynthesisQualityScores
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def required_hard_evidence(self):
        required = {
            "receipt_integrity",
            "authorization_and_lifecycle",
            "numeric_and_unit_consistency",
            "source_traceability",
            "no_unsupported_causality",
            "safe_output",
        }
        if set(self.hard_invariants) != required:
            raise ValueError("hard invariant evidence is incomplete")
        return self


class SynthesisEvaluationVerdict(ProductContract):
    case_id: str
    hard_pass: bool
    core_pass: bool
    quality_score: float = Field(ge=0, le=1)
    passed: bool


class SynthesisGateVerdict(ProductContract):
    gate_version: str = "trusted-synthesis-gate-v1"
    schema_pass_rate: float = Field(ge=0, le=1)
    hard_pass_rate: float = Field(ge=0, le=1)
    core_pass_rate: float = Field(ge=0, le=1)
    semantic_quality: float = Field(ge=0, le=1)
    passed: bool


def evaluate(record: SynthesisEvaluationRecord) -> SynthesisEvaluationVerdict:
    hard = record.schema_valid and all(record.hard_invariants.values())
    quality = record.quality.normalized
    return SynthesisEvaluationVerdict(
        case_id=record.case_id,
        hard_pass=hard,
        core_pass=record.core_task_complete,
        quality_score=quality,
        passed=hard and record.core_task_complete and quality >= 0.85,
    )


def release_gate(records: tuple[SynthesisEvaluationRecord, ...]) -> SynthesisGateVerdict:
    if not records:
        raise ValueError("evaluation records are required")
    verdicts = tuple(evaluate(item) for item in records)
    count = len(records)
    schema_rate = sum(item.schema_valid for item in records) / count
    hard_rate = sum(item.hard_pass for item in verdicts) / count
    core_rate = sum(item.core_pass for item in verdicts) / count
    quality = sum(item.quality_score for item in verdicts) / count
    return SynthesisGateVerdict(
        schema_pass_rate=schema_rate,
        hard_pass_rate=hard_rate,
        core_pass_rate=core_rate,
        semantic_quality=quality,
        passed=(schema_rate == 1 and hard_rate == 1 and core_rate == 1 and quality >= 0.85),
    )


__all__ = [
    "SynthesisEvaluationRecord",
    "SynthesisEvaluationVerdict",
    "SynthesisGateVerdict",
    "SynthesisQualityScores",
    "evaluate",
    "release_gate",
]
