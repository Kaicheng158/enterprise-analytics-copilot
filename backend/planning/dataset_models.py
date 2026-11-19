"""Versioned contracts for bounded Dataset planning and durable results."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.datasets.analysis_models import (
    DatasetAggregateInput,
    DatasetBindingContext,
    DatasetComparisonInput,
    DatasetDerivedMetricInput,
    DatasetProfileInput,
    DatasetQualityInput,
    DatasetRankingInput,
    DatasetSourceSpan,
    DatasetToolResult,
)
from backend.product_facade.models import DatasetRevisionBinding
from backend.tools.models import StrictModel, ToolCall


DATASET_TOOL_INPUTS = {
    "profile_dataset_revision": DatasetProfileInput,
    "aggregate_dataset_revision": DatasetAggregateInput,
    "compare_dataset_revision": DatasetComparisonInput,
    "derive_dataset_metric": DatasetDerivedMetricInput,
    "rank_dataset_revision": DatasetRankingInput,
    "inspect_dataset_quality": DatasetQualityInput,
}
DATASET_TOOL_NAMES = frozenset(DATASET_TOOL_INPUTS)
BLOCKED_ARGUMENT_KEYS = frozenset(
    {"binding", "project_id", "principal", "scope", "trusted_local", "sql", "python", "tool"}
)


class DatasetAgentSettings(StrictModel):
    enabled: bool = False
    policy_version: Literal["dataset-agent-policy-v1"] = "dataset-agent-policy-v1"
    runtime_version: Literal["durable-dataset-agent-v1"] = "durable-dataset-agent-v1"
    max_steps: int = Field(default=7, ge=2, le=7)
    max_tool_calls: int = Field(default=6, ge=1, le=6)
    max_budget_units: int = Field(default=7, ge=2, le=7)
    max_steps_per_run: int = Field(default=8, ge=2, le=8)


class DatasetRequestedOperation(StrictModel):
    operation_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    tool_name: Literal[
        "profile_dataset_revision",
        "aggregate_dataset_revision",
        "compare_dataset_revision",
        "derive_dataset_metric",
        "rank_dataset_revision",
        "inspect_dataset_quality",
    ]
    arguments: dict[str, Any]

    @field_validator("arguments")
    @classmethod
    def authority_is_never_client_supplied(cls, arguments: dict[str, Any]):
        if BLOCKED_ARGUMENT_KEYS.intersection(arguments):
            raise ValueError("operation arguments contain a server-owned field")
        return arguments


class DatasetAgentRequest(StrictModel):
    request_version: Literal["dataset-agent-request-v1"] = "dataset-agent-request-v1"
    project_id: UUID
    binding: DatasetRevisionBinding
    operations: tuple[DatasetRequestedOperation, ...] = Field(min_length=1, max_length=6)

    @field_validator("operations", mode="before")
    @classmethod
    def json_operations(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def operations_are_unique_and_strict(self):
        identifiers = [item.operation_id for item in self.operations]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("operation IDs must be unique")
        binding = self.binding.model_dump(mode="json")
        for item in self.operations:
            DATASET_TOOL_INPUTS[item.tool_name].model_validate(
                {"binding": binding, **item.arguments}, strict=True
            )
        return self

    def tool_input(self, operation: DatasetRequestedOperation):
        return DATASET_TOOL_INPUTS[operation.tool_name].model_validate(
            {"binding": self.binding.model_dump(mode="json"), **operation.arguments},
            strict=True,
        )


class AssembleDatasetResultsSpec(StrictModel):
    operation: Literal["assemble_dataset_results"] = "assemble_dataset_results"
    source_step_ids: tuple[str, ...] = Field(min_length=1, max_length=6)

    @field_validator("source_step_ids", mode="before")
    @classmethod
    def json_sources(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def sources_are_unique(self):
        if len(self.source_step_ids) != len(set(self.source_step_ids)):
            raise ValueError("assembly sources must be unique")
        return self


class DatasetPlanStep(StrictModel):
    step_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    kind: Literal["tool", "compute"]
    description: str = Field(min_length=1, max_length=200)
    depends_on: tuple[str, ...] = Field(default=(), max_length=6)
    request_operation_id: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$"
    )
    tool_call: ToolCall | None = None
    computation: AssembleDatasetResultsSpec | None = None

    @field_validator("depends_on", mode="before")
    @classmethod
    def json_dependencies(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def action_matches_kind(self):
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("step dependencies must be unique")
        if self.kind == "tool":
            if self.tool_call is None or self.computation is not None or self.request_operation_id is None:
                raise ValueError("tool step requires a requested operation and tool call")
        elif self.tool_call is not None or self.computation is None or self.request_operation_id is not None:
            raise ValueError("compute step requires only an assembly computation")
        return self


class DatasetPlan(StrictModel):
    plan_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    version: Literal["dataset-plan-v1"] = "dataset-plan-v1"
    task: Literal["analyze_dataset_revision"] = "analyze_dataset_revision"
    project_id: UUID
    binding: DatasetRevisionBinding
    normalized_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    authorization_version: int = Field(gt=0)
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    steps: tuple[DatasetPlanStep, ...] = Field(min_length=2, max_length=7)

    @field_validator("steps", mode="before")
    @classmethod
    def json_steps(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def final_assembly_is_complete(self):
        identifiers = [step.step_id for step in self.steps]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("step IDs must be unique")
        compute_steps = [step for step in self.steps if step.kind == "compute"]
        if len(compute_steps) != 1 or compute_steps[0] != self.steps[-1]:
            raise ValueError("Dataset plan requires one final assembly step")
        tool_ids = tuple(step.step_id for step in self.steps if step.kind == "tool")
        assembly = compute_steps[0]
        if assembly.depends_on != tool_ids or assembly.computation.source_step_ids != tool_ids:
            raise ValueError("assembly must depend on every tool result in plan order")
        return self


class DatasetPlanIssue(StrictModel):
    code: Literal[
        "plan_request_mismatch",
        "step_limit_exceeded",
        "tool_call_limit_exceeded",
        "budget_limit_exceeded",
        "plan_dependency_unknown",
        "plan_cycle_detected",
        "plan_dependency_order_invalid",
        "action_not_authorized",
        "tool_input_invalid",
        "field_not_found",
        "dataset_binding_changed",
    ]
    operation_id: str | None = None
    fields: tuple[str, ...] = Field(default=(), max_length=16)


class DatasetPlanValidationResult(StrictModel):
    status: Literal["valid", "invalid"]
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_order: tuple[str, ...]
    issues: tuple[DatasetPlanIssue, ...]

    @model_validator(mode="after")
    def status_is_coherent(self):
        if self.status == "valid" and (not self.execution_order or self.issues):
            raise ValueError("valid plan requires an order and no issues")
        if self.status == "invalid" and (self.execution_order or not self.issues):
            raise ValueError("invalid plan requires issues and no order")
        return self


class DatasetAgentClarification(StrictModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,79}$")
    fields: tuple[str, ...] = Field(default=(), max_length=16)


class DatasetAgentFinalResult(StrictModel):
    result_version: Literal["dataset-agent-result-v1"] = "dataset-agent-result-v1"
    plan_id: str
    project_id: UUID
    binding: DatasetRevisionBinding
    normalized_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["completed", "needs_clarification", "blocked"]
    results: tuple[DatasetToolResult, ...] = Field(min_length=1, max_length=6)
    clarifications: tuple[DatasetAgentClarification, ...] = Field(default=(), max_length=32)
    sources: tuple[DatasetSourceSpan, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def result_status_is_coherent(self):
        if self.status == "needs_clarification" and not self.clarifications:
            raise ValueError("clarification status requires consolidated questions")
        if self.status == "completed" and self.clarifications:
            raise ValueError("completed result cannot contain clarification requests")
        return self


class DatasetAgentTaskView(StrictModel):
    response_version: Literal["dataset-agent-task-v1"] = "dataset-agent-task-v1"
    task_id: UUID
    status: Literal["queued", "running", "needs_reconciliation", "completed", "failed"]
    created: bool | None = None
    request: DatasetAgentRequest
    completed_steps: int = Field(ge=0, le=7)
    total_steps: int = Field(ge=2, le=7)
    state_version: int = Field(ge=0)
    result: DatasetAgentFinalResult | None = None
    error_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,79}$")

    @model_validator(mode="after")
    def terminal_fields_are_safe(self):
        if self.completed_steps > self.total_steps:
            raise ValueError("completed steps exceed plan")
        if self.status == "completed" and (self.result is None or self.error_code is not None):
            raise ValueError("completed task requires a result")
        if self.status != "completed" and self.result is not None:
            raise ValueError("only completed task exposes a result")
        if self.status in {"failed", "needs_reconciliation"} and self.error_code is None:
            raise ValueError("stopped task requires a safe error code")
        if self.status not in {"failed", "needs_reconciliation"} and self.error_code is not None:
            raise ValueError("active/successful task cannot contain an error")
        return self


__all__ = [name for name in globals() if name.startswith("Dataset") or name == "AssembleDatasetResultsSpec"]
