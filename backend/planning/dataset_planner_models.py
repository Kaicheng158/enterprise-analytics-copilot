"""Strict contracts for natural-language Dataset planning without data authority."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.product_facade.models import DatasetRevisionBinding
from backend.tools.models import StrictModel

from .dataset_models import DatasetAgentRequest, DatasetPlan, DatasetPlanIssue
from .structured_provider import PlannerGenerationConfig, PlannerUsage


class DatasetPlannerQuestion(StrictModel):
    request_version: Literal["dataset-planner-question-v1"] = "dataset-planner-question-v1"
    project_id: UUID
    binding: DatasetRevisionBinding
    question: str = Field(min_length=1, max_length=2_000)
    preferred_language: Literal["auto", "en", "zh"] = "auto"

    @field_validator("question")
    @classmethod
    def bounded_visible_text(cls, value: str) -> str:
        if value != value.strip() or any(ord(character) < 32 and character not in "\n\t" for character in value):
            raise ValueError("invalid Dataset planning question")
        return value


class DatasetPlannerField(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    kind: Literal["text", "decimal", "money", "date", "boolean"]
    required: bool
    unit: str | None = Field(default=None, max_length=32)


class DatasetPlannerTable(StrictModel):
    table_index: int = Field(ge=1, le=4)
    fields: tuple[DatasetPlannerField, ...] = Field(min_length=1, max_length=64)

    @field_validator("fields", mode="before")
    @classmethod
    def json_fields(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def unique_fields(self):
        if len({field.name for field in self.fields}) != len(self.fields):
            raise ValueError("Dataset Planner fields must be unique")
        return self


class DatasetMetricDefinition(StrictModel):
    metric_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    description: str = Field(min_length=1, max_length=240)
    formula: str = Field(min_length=1, max_length=240)
    fields: tuple[str, ...] = Field(min_length=1, max_length=8)
    unit: str | None = Field(default=None, max_length=32)

    @field_validator("fields", mode="before")
    @classmethod
    def json_fields(cls, value):
        return tuple(value) if isinstance(value, list) else value


class DatasetPlannerCatalog(StrictModel):
    catalog_version: Literal["dataset-planner-catalog-v1"] = "dataset-planner-catalog-v1"
    tables: tuple[DatasetPlannerTable, ...] = Field(min_length=1, max_length=4)
    metrics: tuple[DatasetMetricDefinition, ...] = Field(default=(), max_length=32)

    @field_validator("tables", "metrics", mode="before")
    @classmethod
    def json_tuples(cls, value):
        return tuple(value) if isinstance(value, list) else value


class DatasetPlannedOperation(StrictModel):
    operation_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    tool_name: Literal[
        "profile_dataset_revision",
        "aggregate_dataset_revision",
        "compare_dataset_revision",
        "derive_dataset_metric",
        "rank_dataset_revision",
        "inspect_dataset_quality",
    ]
    description: str = Field(min_length=1, max_length=200)
    arguments: dict[str, Any]
    depends_on: tuple[str, ...] = Field(default=(), max_length=5)

    @field_validator("depends_on", mode="before")
    @classmethod
    def json_dependencies(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def dependency_list_is_unique(self):
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("operation dependencies must be unique")
        return self


class DatasetPlannerDraft(StrictModel):
    response_version: Literal["dataset-planner-draft-v1"] = "dataset-planner-draft-v1"
    outcome: Literal["plan", "needs_clarification"]
    operations: tuple[DatasetPlannedOperation, ...] = Field(default=(), max_length=6)
    clarifications: tuple[str, ...] = Field(default=(), max_length=8)

    @field_validator("operations", "clarifications", mode="before")
    @classmethod
    def json_tuples(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @field_validator("clarifications")
    @classmethod
    def safe_clarifications(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() or value != value.strip() or len(value) > 300 for value in values):
            raise ValueError("invalid clarification")
        return values

    @model_validator(mode="after")
    def outcome_is_coherent(self):
        if self.outcome == "plan" and (not self.operations or self.clarifications):
            raise ValueError("plan outcome requires operations only")
        if self.outcome == "needs_clarification" and (self.operations or not self.clarifications):
            raise ValueError("clarification outcome requires questions only")
        if len({item.operation_id for item in self.operations}) != len(self.operations):
            raise ValueError("operation IDs must be unique")
        return self


class DatasetPlannerTelemetry(StrictModel):
    provider: Literal["deepseek"] = "deepseek"
    model: Literal["deepseek-flash"] = "deepseek-flash"
    prompt_version: Literal["dataset-planner-v2"] = "dataset-planner-v2"
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_id: str = Field(min_length=1, max_length=128)
    finish_reason: Literal["stop"] = "stop"
    usage: PlannerUsage
    latency_ms: int = Field(ge=0)
    estimated_cost_usd: str = Field(pattern=r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")
    retry_count: Literal[0] = 0
    generation_config: PlannerGenerationConfig


class DatasetPlannerOutcome(StrictModel):
    outcome_version: Literal["dataset-planner-outcome-v1"] = "dataset-planner-outcome-v1"
    status: Literal["planned", "needs_clarification", "rejected"]
    question_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    clarifications: tuple[str, ...] = Field(default=(), max_length=8)
    request: DatasetAgentRequest | None = None
    plan: DatasetPlan | None = None
    issues: tuple[DatasetPlanIssue, ...] = Field(default=(), max_length=32)
    telemetry: DatasetPlannerTelemetry

    @model_validator(mode="after")
    def status_is_coherent(self):
        if self.status == "planned" and (self.request is None or self.plan is None or self.clarifications or self.issues):
            raise ValueError("planned outcome requires request and Plan only")
        if self.status == "needs_clarification" and (not self.clarifications or self.request is not None or self.plan is not None or self.issues):
            raise ValueError("clarification outcome is inconsistent")
        if self.status == "rejected" and (not self.issues or self.plan is not None or self.request is None or self.clarifications):
            raise ValueError("rejected outcome is inconsistent")
        return self


__all__ = [name for name in globals() if name.startswith("Dataset")]
