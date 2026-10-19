"""Strict provider-independent contracts for bounded multi-step planning."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from backend.tools.models import StrictModel, ToolCall


StepId = str
TaskName = Literal["compare_quarterly_sales"]
Region = Literal["all", "north", "south"]
Period = Literal["2026-Q1", "2026-Q2", "2026-Q3"]


class PlanningLimits(StrictModel):
    max_steps: int = Field(default=3, ge=1, le=8)
    max_tool_calls: int = Field(default=2, ge=0, le=4)
    timeout_ms: int = Field(default=15_000, ge=1, le=30_000)
    max_budget_units: int = Field(default=3, ge=1, le=12)


class PlanningRequest(StrictModel):
    task: TaskName = "compare_quarterly_sales"
    baseline_period: Period = "2026-Q1"
    comparison_period: Period = "2026-Q2"
    region: Region = "all"

    @model_validator(mode="after")
    def periods_must_differ(self):
        if self.baseline_period == self.comparison_period:
            raise ValueError("comparison periods must differ")
        return self


class CompareSalesGrowthSpec(StrictModel):
    operation: Literal["compare_sales_growth"] = "compare_sales_growth"
    baseline_step_id: StepId = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    comparison_step_id: StepId = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    baseline_period: Period
    comparison_period: Period
    region: Region


class PlanStep(StrictModel):
    step_id: StepId = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    kind: Literal["tool", "compute"]
    description: str = Field(min_length=1, max_length=200)
    depends_on: tuple[StepId, ...] = Field(default=(), max_length=8)
    tool_call: ToolCall | None = None
    computation: CompareSalesGrowthSpec | None = None

    @field_validator("depends_on")
    @classmethod
    def dependencies_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("step dependencies must be unique")
        return value

    @model_validator(mode="after")
    def action_matches_kind(self):
        if self.step_id in self.depends_on:
            raise ValueError("step cannot depend on itself")
        if self.kind == "tool":
            if self.tool_call is None or self.computation is not None:
                raise ValueError("tool step requires only tool_call")
        elif self.tool_call is not None or self.computation is None:
            raise ValueError("compute step requires only computation")
        return self


class Plan(StrictModel):
    plan_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    version: Literal["bounded-plan-v1"] = "bounded-plan-v1"
    task: TaskName
    baseline_period: Period
    comparison_period: Period
    region: Region
    steps: tuple[PlanStep, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def graph_is_ordered_and_consistent(self):
        if self.baseline_period == self.comparison_period:
            raise ValueError("comparison periods must differ")
        seen: set[str] = set()
        for step in self.steps:
            if step.step_id in seen:
                raise ValueError("step IDs must be unique")
            if any(dependency not in seen for dependency in step.depends_on):
                raise ValueError("dependencies must reference earlier steps")
            seen.add(step.step_id)
        compute_steps = [step for step in self.steps if step.kind == "compute"]
        if len(compute_steps) != 1 or compute_steps[0] != self.steps[-1]:
            raise ValueError("plan requires one final compute step")
        spec = compute_steps[0].computation
        if (
            spec.baseline_period != self.baseline_period
            or spec.comparison_period != self.comparison_period
            or spec.region != self.region
            or set(compute_steps[0].depends_on)
            != {spec.baseline_step_id, spec.comparison_step_id}
        ):
            raise ValueError("compute scope must match plan and dependencies")
        return self


class PlanningError(StrictModel):
    code: Literal[
        "plan_invalid",
        "plan_provider_error",
        "plan_request_mismatch",
        "step_limit_exceeded",
        "tool_call_limit_exceeded",
        "budget_limit_exceeded",
        "plan_timeout",
        "action_not_authorized",
        "dependency_failed",
        "tool_execution_failed",
        "intermediate_output_invalid",
        "comparison_scope_mismatch",
        "growth_rate_undefined",
        "state_consistency_error",
    ]
    message: str = Field(min_length=1, max_length=200)


class StepExecutionState(StrictModel):
    step_id: StepId
    kind: Literal["tool", "compute"]
    status: Literal["pending", "running", "completed", "failed", "skipped"]
    attempts: int = Field(ge=0, le=1)
    output: dict[str, Any] | None = None
    error: PlanningError | None = None
    latency_ms: int = Field(default=0, ge=0)
    tool_call_id: str | None = None

    @model_validator(mode="after")
    def fields_match_status(self):
        if self.status == "completed" and (self.output is None or self.error is not None):
            raise ValueError("completed step requires output and no error")
        if self.status == "failed" and (self.output is not None or self.error is None):
            raise ValueError("failed step requires error and no output")
        if self.status in {"pending", "running", "skipped"} and (
            self.output is not None or self.error is not None
        ):
            raise ValueError("nonterminal step cannot contain output/error")
        if self.kind == "compute" and self.tool_call_id is not None:
            raise ValueError("compute step cannot contain tool call ID")
        return self


class AgentState(StrictModel):
    plan_id: str
    status: Literal["pending", "running", "completed", "failed"]
    current_step_id: StepId | None
    step_states: tuple[StepExecutionState, ...]
    completed_step_ids: tuple[StepId, ...]
    steps_executed: int = Field(ge=0)
    tool_calls_used: int = Field(ge=0)
    budget_units_used: int = Field(ge=0)
    elapsed_ms: int = Field(ge=0)
    error: PlanningError | None = None

    @model_validator(mode="after")
    def state_is_internally_consistent(self):
        ids = [step.step_id for step in self.step_states]
        if len(ids) != len(set(ids)):
            raise ValueError("state step IDs must be unique")
        completed = tuple(
            step.step_id for step in self.step_states if step.status == "completed"
        )
        if completed != self.completed_step_ids or self.steps_executed != len(completed):
            raise ValueError("completed step accounting mismatch")
        attempted = [step for step in self.step_states if step.attempts == 1]
        if self.budget_units_used != len(attempted):
            raise ValueError("budget accounting mismatch")
        if self.tool_calls_used != sum(step.kind == "tool" for step in attempted):
            raise ValueError("tool call accounting mismatch")
        running = [step.step_id for step in self.step_states if step.status == "running"]
        if running != ([self.current_step_id] if self.current_step_id else []):
            raise ValueError("current step accounting mismatch")
        if self.status == "completed" and (
            self.error is not None
            or self.current_step_id is not None
            or len(completed) != len(self.step_states)
        ):
            raise ValueError("completed agent state is inconsistent")
        if self.status == "failed" and (self.error is None or self.current_step_id is not None):
            raise ValueError("failed agent state requires terminal error")
        if self.status in {"pending", "running"} and self.error is not None:
            raise ValueError("nonterminal agent state cannot contain error")
        return self


class SalesGrowthComparison(StrictModel):
    baseline_period: Period
    comparison_period: Period
    region: Region
    currency: Literal["USD"]
    baseline_total_sales: Decimal = Field(ge=0, decimal_places=2, allow_inf_nan=False)
    comparison_total_sales: Decimal = Field(ge=0, decimal_places=2, allow_inf_nan=False)
    absolute_change: Decimal = Field(decimal_places=2, allow_inf_nan=False)
    growth_rate_percent: Decimal = Field(decimal_places=2, allow_inf_nan=False)
    source_relation: Literal["analytics.sales_orders"]
    dataset_version: Literal["synthetic-sales-db-v1"]


class PlanExecutionResult(StrictModel):
    status: Literal["completed", "failed"]
    plan: Plan | None
    state: AgentState | None
    state_history: tuple[AgentState, ...]
    output: SalesGrowthComparison | None
    error: PlanningError | None
    provider_calls: int = Field(ge=0, le=1)

    @model_validator(mode="after")
    def result_is_consistent(self):
        if self.status == "completed" and (
            self.plan is None
            or self.state is None
            or self.state.status != "completed"
            or self.output is None
            or self.error is not None
        ):
            raise ValueError("completed result is inconsistent")
        if self.status == "failed" and (self.output is not None or self.error is None):
            raise ValueError("failed result requires error and no output")
        return self
