"""Strict, additive contracts for PV2-04e trusted result synthesis."""

from __future__ import annotations

from typing import Literal, Protocol
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.product_facade.models import (
    AnalysisReportV1,
    AnalysisSourceReference,
    CalculationValue,
    ChartSpecV1,
    ProductContract,
)


class TrustedSynthesisSettings(ProductContract):
    enabled: bool = False
    policy_version: Literal["trusted-result-synthesis-v1"] = "trusted-result-synthesis-v1"
    validator_version: Literal["trusted-result-validator-v1"] = "trusted-result-validator-v1"
    max_calculations: int = Field(default=500, ge=1, le=500)
    max_charts: int = Field(default=3, ge=0, le=3)
    max_report_sections: int = Field(default=8, ge=1, le=8)


class TrustedSynthesisRequestV1(ProductContract):
    request_version: Literal["trusted-synthesis-request-v1"] = "trusted-synthesis-request-v1"
    task_id: UUID
    question: str = Field(min_length=1, max_length=2000)
    output_mode: Literal["auto", "short_answer", "chart", "full_report"] = "auto"
    language: Literal["en", "zh"] = "en"

    @field_validator("question")
    @classmethod
    def safe_question(cls, value: str) -> str:
        if value != value.strip() or any(ord(char) < 32 and char not in "\n\t" for char in value):
            raise ValueError("invalid question")
        return value


class CalculationDimensionV2(ProductContract):
    key: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1, max_length=256)


class TrustedCalculationV2(ProductContract):
    result_version: Literal["trusted-calculation-v2"] = "trusted-calculation-v2"
    calculation_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    operation: Literal["profile", "aggregate", "compare", "derive", "rank", "quality"]
    label: str = Field(min_length=1, max_length=160)
    value: CalculationValue | None = None
    dimensions: tuple[CalculationDimensionV2, ...] = Field(default=(), max_length=8)
    parameters_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sources: tuple[AnalysisSourceReference, ...] = Field(min_length=1, max_length=32)
    limitation_codes: tuple[str, ...] = Field(default=(), max_length=16)


class ClaimValueReference(ProductContract):
    calculation_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    value: str = Field(pattern=r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")
    unit: str = Field(min_length=1, max_length=32)


class TrustedClaimV1(ProductContract):
    claim_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    kind: Literal["fact", "interpretation", "proposed_action"]
    text: str = Field(min_length=1, max_length=1000)
    value_references: tuple[ClaimValueReference, ...] = Field(default=(), max_length=16)
    causal: bool = False

    @model_validator(mode="after")
    def facts_are_grounded(self):
        if self.kind == "fact" and not self.value_references:
            raise ValueError("fact claim requires a calculation reference")
        return self


class TrustedAnswerV1(ProductContract):
    answer_version: Literal["trusted-answer-v1"] = "trusted-answer-v1"
    summary: str = Field(min_length=1, max_length=2000)
    claims: tuple[TrustedClaimV1, ...] = Field(min_length=1, max_length=64)
    limitations: tuple[str, ...] = Field(default=(), max_length=32)


class ReceiptReferenceV1(ProductContract):
    step_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    action_name: str = Field(min_length=1, max_length=100)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_call_id: str | None = Field(default=None, max_length=160)


class SynthesisTraceV1(ProductContract):
    trace_version: Literal["trusted-synthesis-trace-v1"] = "trusted-synthesis-trace-v1"
    task_id: UUID
    project_id: UUID
    dataset_revision_id: UUID
    normalized_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_id: str = Field(min_length=1, max_length=64)
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    state_version: int = Field(ge=0)
    receipts: tuple[ReceiptReferenceV1, ...] = Field(min_length=1, max_length=7)
    sources: tuple[AnalysisSourceReference, ...] = Field(min_length=1, max_length=64)


class SynthesisDraftV1(ProductContract):
    draft_version: Literal["trusted-synthesis-draft-v1"] = "trusted-synthesis-draft-v1"
    summary: str = Field(min_length=1, max_length=2000)
    claims: tuple[TrustedClaimV1, ...] = Field(min_length=1, max_length=64)
    limitations: tuple[str, ...] = Field(default=(), max_length=32)


class TrustedSynthesisResponseV1(ProductContract):
    response_version: Literal["trusted-synthesis-response-v1"] = "trusted-synthesis-response-v1"
    mode: Literal["short_answer", "answer_with_chart", "full_report"]
    answer: TrustedAnswerV1
    calculations: tuple[TrustedCalculationV2, ...] = Field(min_length=1, max_length=500)
    charts: tuple[ChartSpecV1, ...] = Field(default=(), max_length=3)
    report: AnalysisReportV1 | None = None
    trace: SynthesisTraceV1
    validator_version: Literal["trusted-result-validator-v1"] = "trusted-result-validator-v1"

    @model_validator(mode="after")
    def deliverables_match_mode(self):
        if self.mode == "short_answer" and (self.charts or self.report is not None):
            raise ValueError("short answer cannot expose charts or a report")
        if self.mode == "answer_with_chart" and (not self.charts or self.report is not None):
            raise ValueError("chart mode requires charts only")
        if self.mode == "full_report" and self.report is None:
            raise ValueError("full report mode requires a report")
        return self


class ResultSynthesizer(Protocol):
    def synthesize(
        self,
        request: TrustedSynthesisRequestV1,
        calculations: tuple[TrustedCalculationV2, ...],
    ) -> SynthesisDraftV1: ...


class SynthesisFailure(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
