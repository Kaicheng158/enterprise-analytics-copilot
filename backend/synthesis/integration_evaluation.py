"""Independent PV2-04f end-to-end evaluation contracts and release gate."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from backend.product_facade.models import ProductContract


HARD_INVARIANTS = frozenset(
    {
        "session_identity",
        "project_dataset_authorization",
        "planner_validator_alignment",
        "runtime_receipt_integrity",
        "synthesis_grounding",
        "result_cross_field_consistency",
        "lifecycle_and_revocation",
        "safe_output",
    }
)


class IntegrationQualityScores(ProductContract):
    task_completion: int = Field(ge=0, le=2)
    relevance_actionability: int = Field(ge=0, le=2)
    clarity_concision: int = Field(ge=0, le=2)

    @property
    def normalized(self) -> float:
        return (
            self.task_completion
            + self.relevance_actionability
            + self.clarity_concision
        ) / 6


class IntegrationCaseRecord(ProductContract):
    evaluation_version: Literal["pv2-04f-integration-eval-v1"] = (
        "pv2-04f-integration-eval-v1"
    )
    case_id: str = Field(pattern=r"^[a-z][a-z0-9_/-]{1,95}$")
    execution_mode: Literal[
        "offline_deterministic",
        "mock_http",
        "isolated_postgresql",
        "historical_live_reference",
    ]
    planner_mode: Literal[
        "deterministic_fake", "mock_deepseek_adapter", "frozen_live_deepseek_reference"
    ]
    synthesizer_mode: Literal["fake"] = "fake"
    expected_outcome: Literal["completed", "clarification", "rejected", "blocked"]
    observed_outcome: Literal["completed", "clarification", "rejected", "blocked"]
    schema_valid: bool
    hard_invariants: dict[str, bool]
    core_task_complete: bool
    quality: IntegrationQualityScores
    failure_class: Literal[
        "session",
        "authorization",
        "dataset_lifecycle",
        "planner_intent",
        "plan_schema",
        "validator",
        "durable_runtime",
        "receipt_integrity",
        "synthesis_grounding",
        "chart_report_consistency",
        "provider",
        "budget",
        "evidence_integrity",
    ] | None = None
    reproduction_steps: tuple[str, ...] = Field(min_length=1, max_length=12)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=16)
    version_refs: dict[str, str]
    sha_refs: dict[str, str]
    provider_calls: int = Field(default=0, ge=0, le=4)
    tokens: int = Field(default=0, ge=0)
    provider_latency_ms: int = Field(default=0, ge=0)
    estimated_cost_usd: str = Field(default="0", pattern=r"^(0|0\.[0-9]+)$")

    @model_validator(mode="after")
    def evidence_is_complete(self):
        if set(self.hard_invariants) != HARD_INVARIANTS:
            raise ValueError("integration hard-invariant evidence is incomplete")
        if not self.version_refs or not self.sha_refs:
            raise ValueError("version and SHA evidence are required")
        if self.observed_outcome == self.expected_outcome and self.failure_class is not None:
            raise ValueError("successful expected outcome cannot have a failure class")
        if self.observed_outcome != self.expected_outcome and self.failure_class is None:
            raise ValueError("unexpected outcome requires a failure class")
        if self.planner_mode != "frozen_live_deepseek_reference" and self.provider_calls:
            raise ValueError("offline PV2-04f records cannot claim paid provider calls")
        return self


class IntegrationCaseVerdict(ProductContract):
    case_id: str
    hard_pass: bool
    core_pass: bool
    quality_score: float = Field(ge=0, le=1)
    passed: bool


class IntegrationGateVerdict(ProductContract):
    gate_version: Literal["pv2-04f-integration-gate-v1"] = (
        "pv2-04f-integration-gate-v1"
    )
    case_count: int = Field(gt=0)
    schema_pass_rate: float = Field(ge=0, le=1)
    hard_pass_rate: float = Field(ge=0, le=1)
    core_pass_rate: float = Field(ge=0, le=1)
    semantic_quality: float = Field(ge=0, le=1)
    unexpected_failure_count: int = Field(ge=0)
    passed: bool


def evaluate_case(record: IntegrationCaseRecord) -> IntegrationCaseVerdict:
    hard = record.schema_valid and all(record.hard_invariants.values())
    core = record.core_task_complete and record.observed_outcome == record.expected_outcome
    quality = record.quality.normalized
    return IntegrationCaseVerdict(
        case_id=record.case_id,
        hard_pass=hard,
        core_pass=core,
        quality_score=quality,
        passed=hard and core and quality >= 0.85,
    )


def integration_release_gate(
    records: tuple[IntegrationCaseRecord, ...],
) -> IntegrationGateVerdict:
    if not records:
        raise ValueError("integration records are required")
    verdicts = tuple(evaluate_case(item) for item in records)
    count = len(records)
    schema = sum(item.schema_valid for item in records) / count
    hard = sum(item.hard_pass for item in verdicts) / count
    core = sum(item.core_pass for item in verdicts) / count
    quality = sum(item.quality_score for item in verdicts) / count
    failures = sum(item.observed_outcome != item.expected_outcome for item in records)
    return IntegrationGateVerdict(
        case_count=count,
        schema_pass_rate=schema,
        hard_pass_rate=hard,
        core_pass_rate=core,
        semantic_quality=quality,
        unexpected_failure_count=failures,
        passed=(schema == 1 and hard == 1 and core == 1 and quality >= 0.85 and failures == 0),
    )


__all__ = [
    "HARD_INVARIANTS",
    "IntegrationCaseRecord",
    "IntegrationCaseVerdict",
    "IntegrationGateVerdict",
    "IntegrationQualityScores",
    "evaluate_case",
    "integration_release_gate",
]
