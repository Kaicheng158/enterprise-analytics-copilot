"""Bounded deterministic executor for server-authorized plans."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import time
from typing import Callable, Literal, Mapping, Protocol

from pydantic import Field, ValidationError, model_validator

from backend.tools.executor import ToolExecutor
from backend.tools.models import StrictModel, ToolExecutionContext

from .models import (
    AgentState,
    CompareSalesGrowthSpec,
    Plan,
    PlanExecutionResult,
    PlanningError,
    PlanningLimits,
    SalesGrowthComparison,
    StepExecutionState,
)
SAFE_MESSAGES = {
    "step_limit_exceeded": "Plan step limit exceeded",
    "tool_call_limit_exceeded": "Plan tool call limit exceeded",
    "budget_limit_exceeded": "Plan execution budget exceeded",
    "plan_timeout": "Plan execution timed out",
    "action_not_authorized": "Plan action is not authorized",
    "dependency_failed": "Required step dependency is unavailable",
    "tool_execution_failed": "A planned tool execution failed",
    "intermediate_output_invalid": "Intermediate tool output is invalid",
    "comparison_scope_mismatch": "Sales summaries do not have a comparable scope",
    "growth_rate_undefined": "Growth rate is undefined for a zero baseline",
    "state_consistency_error": "Agent state consistency check failed",
}


class ExecutableTool(Protocol):
    def execute(self, call, context): ...


@dataclass(frozen=True)
class PlanningPolicy:
    allowed_tools: frozenset[str] = frozenset({"get_postgres_sales_summary"})
    allowed_computations: frozenset[str] = frozenset({"compare_sales_growth"})


class SalesSourceEvidence(StrictModel):
    kind: Literal["postgresql"]
    relation: Literal["analytics.sales_orders"]
    dataset_version: Literal["synthetic-sales-db-v1"]
    read_only: Literal[True]


class SalesSummaryEvidence(StrictModel):
    period: Literal["2026-Q1", "2026-Q2", "2026-Q3"]
    region: Literal["all", "north", "south"]
    currency: Literal["USD"]
    order_count: int = Field(ge=0)
    total_sales: str = Field(pattern=r"^(0|[1-9][0-9]*)\.[0-9]{2}$")
    average_order_value: str | None = Field(
        pattern=r"^(0|[1-9][0-9]*)\.[0-9]{2}$"
    )
    data_source: SalesSourceEvidence

    @model_validator(mode="after")
    def average_matches_order_count(self):
        if (self.order_count == 0) != (self.average_order_value is None):
            raise ValueError("average order value must match order count semantics")
        return self


Clock = Callable[[], float]


class BoundedPlanExecutor:
    TOOL_BUDGET_UNITS = 1
    COMPUTE_BUDGET_UNITS = 1

    def __init__(
        self,
        tool_executor: ToolExecutor | ExecutableTool,
        *,
        limits: PlanningLimits | None = None,
        policy: PlanningPolicy | None = None,
        clock: Clock = time.monotonic,
    ):
        self._tool_executor = tool_executor
        self._limits = limits or PlanningLimits()
        self._policy = policy or PlanningPolicy()
        self._clock = clock

    def execute(
        self,
        plan: Plan,
        context: ToolExecutionContext,
        *,
        provider_calls: int = 0,
    ) -> PlanExecutionResult:
        started = self._clock()
        states = {
            step.step_id: StepExecutionState(
                step_id=step.step_id,
                kind=step.kind,
                status="pending",
                attempts=0,
            )
            for step in plan.steps
        }
        completed: list[str] = []
        tool_calls = 0
        budget = 0
        history: list[AgentState] = []

        def snapshot(status, current=None, error=None):
            state = AgentState(
                plan_id=plan.plan_id,
                status=status,
                current_step_id=current,
                step_states=tuple(states[step.step_id] for step in plan.steps),
                completed_step_ids=tuple(completed),
                steps_executed=len(completed),
                tool_calls_used=tool_calls,
                budget_units_used=budget,
                elapsed_ms=max(0, round((self._clock() - started) * 1000)),
                error=error,
            )
            history.append(state)
            return state

        history.append(
            AgentState(
                plan_id=plan.plan_id,
                status="pending",
                current_step_id=None,
                step_states=tuple(states[step.step_id] for step in plan.steps),
                completed_step_ids=(),
                steps_executed=0,
                tool_calls_used=0,
                budget_units_used=0,
                elapsed_ms=0,
                error=None,
            )
        )

        rejection = self._validate_plan(plan)
        if rejection:
            self._skip_pending(states)
            final = snapshot("failed", error=rejection)
            return self._failed(plan, final, history, rejection, provider_calls)

        for step in plan.steps:
            timeout = self._timeout_error(started)
            if timeout:
                self._skip_pending(states)
                final = snapshot("failed", error=timeout)
                return self._failed(plan, final, history, timeout, provider_calls)
            if any(dependency not in completed for dependency in step.depends_on):
                error = self._error("dependency_failed")
                states[step.step_id] = StepExecutionState(
                    step_id=step.step_id,
                    kind=step.kind,
                    status="failed",
                    attempts=0,
                    error=error,
                )
                self._skip_pending(states)
                final = snapshot("failed", error=error)
                return self._failed(plan, final, history, error, provider_calls)

            unit_cost = (
                self.TOOL_BUDGET_UNITS if step.kind == "tool" else self.COMPUTE_BUDGET_UNITS
            )
            if budget + unit_cost > self._limits.max_budget_units:
                error = self._error("budget_limit_exceeded")
                states[step.step_id] = StepExecutionState(
                    step_id=step.step_id,
                    kind=step.kind,
                    status="failed",
                    attempts=0,
                    error=error,
                )
                self._skip_pending(states)
                final = snapshot("failed", error=error)
                return self._failed(plan, final, history, error, provider_calls)

            budget += unit_cost
            if step.kind == "tool":
                tool_calls += 1
            states[step.step_id] = StepExecutionState(
                step_id=step.step_id,
                kind=step.kind,
                status="running",
                attempts=1,
            )
            snapshot("running", current=step.step_id)
            step_started = self._clock()
            if step.kind == "tool":
                result = self._tool_executor.execute(step.tool_call, context)
                if result.status != "success":
                    error = self._error("tool_execution_failed")
                    states[step.step_id] = StepExecutionState(
                        step_id=step.step_id,
                        kind=step.kind,
                        status="failed",
                        attempts=1,
                        error=error,
                        latency_ms=max(0, round((self._clock() - step_started) * 1000)),
                        tool_call_id=result.call_id,
                    )
                    self._skip_pending(states)
                    final = snapshot("failed", error=error)
                    return self._failed(plan, final, history, error, provider_calls)
                output = deepcopy(result.output)
                tool_call_id = result.call_id
            else:
                try:
                    output = self._compute_growth(step.computation, states)
                except _StepFailure as failure:
                    states[step.step_id] = StepExecutionState(
                        step_id=step.step_id,
                        kind=step.kind,
                        status="failed",
                        attempts=1,
                        error=failure.error,
                        latency_ms=max(0, round((self._clock() - step_started) * 1000)),
                    )
                    self._skip_pending(states)
                    final = snapshot("failed", error=failure.error)
                    return self._failed(
                        plan, final, history, failure.error, provider_calls
                    )
                tool_call_id = None

            timeout = self._timeout_error(started)
            if timeout:
                states[step.step_id] = StepExecutionState(
                    step_id=step.step_id,
                    kind=step.kind,
                    status="failed",
                    attempts=1,
                    error=timeout,
                    latency_ms=max(0, round((self._clock() - step_started) * 1000)),
                    tool_call_id=tool_call_id,
                )
                self._skip_pending(states)
                final = snapshot("failed", error=timeout)
                return self._failed(plan, final, history, timeout, provider_calls)

            states[step.step_id] = StepExecutionState(
                step_id=step.step_id,
                kind=step.kind,
                status="completed",
                attempts=1,
                output=output,
                latency_ms=max(0, round((self._clock() - step_started) * 1000)),
                tool_call_id=tool_call_id,
            )
            completed.append(step.step_id)
            snapshot("running")

        try:
            output = SalesGrowthComparison.model_validate(
                states[plan.steps[-1].step_id].output, strict=True
            )
            final = snapshot("completed")
        except (ValidationError, KeyError, TypeError):
            error = self._error("state_consistency_error")
            final = snapshot("failed", error=error)
            return self._failed(plan, final, history, error, provider_calls)
        return PlanExecutionResult(
            status="completed",
            plan=plan,
            state=final,
            state_history=tuple(history),
            output=output,
            error=None,
            provider_calls=provider_calls,
        )

    def _validate_plan(self, plan: Plan) -> PlanningError | None:
        tool_steps = [step for step in plan.steps if step.kind == "tool"]
        if len(plan.steps) > self._limits.max_steps:
            return self._error("step_limit_exceeded")
        if len(tool_steps) > self._limits.max_tool_calls:
            return self._error("tool_call_limit_exceeded")
        if len(plan.steps) > self._limits.max_budget_units:
            return self._error("budget_limit_exceeded")
        for step in plan.steps:
            if step.kind == "tool" and step.tool_call.name not in self._policy.allowed_tools:
                return self._error("action_not_authorized")
            if (
                step.kind == "compute"
                and step.computation.operation not in self._policy.allowed_computations
            ):
                return self._error("action_not_authorized")
        return None

    def _compute_growth(
        self,
        spec: CompareSalesGrowthSpec,
        states: Mapping[str, StepExecutionState],
    ) -> dict:
        try:
            baseline = SalesSummaryEvidence.model_validate(
                states[spec.baseline_step_id].output, strict=True
            )
            comparison = SalesSummaryEvidence.model_validate(
                states[spec.comparison_step_id].output, strict=True
            )
            baseline_total = Decimal(baseline.total_sales)
            comparison_total = Decimal(comparison.total_sales)
        except (ValidationError, KeyError, TypeError, InvalidOperation):
            raise _StepFailure(self._error("intermediate_output_invalid")) from None
        if (
            baseline.period != spec.baseline_period
            or comparison.period != spec.comparison_period
            or baseline.region != spec.region
            or comparison.region != spec.region
            or baseline.currency != comparison.currency
            or baseline.data_source != comparison.data_source
        ):
            raise _StepFailure(self._error("comparison_scope_mismatch"))
        if baseline_total == 0:
            raise _StepFailure(self._error("growth_rate_undefined"))
        change = (comparison_total - baseline_total).quantize(Decimal("0.01"))
        growth = (
            change / baseline_total * Decimal(100)
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return SalesGrowthComparison(
            baseline_period=baseline.period,
            comparison_period=comparison.period,
            region=baseline.region,
            currency=baseline.currency,
            baseline_total_sales=baseline_total.quantize(Decimal("0.01")),
            comparison_total_sales=comparison_total.quantize(Decimal("0.01")),
            absolute_change=change,
            growth_rate_percent=growth,
            source_relation=baseline.data_source.relation,
            dataset_version=baseline.data_source.dataset_version,
        ).model_dump(mode="python")

    def _timeout_error(self, started: float) -> PlanningError | None:
        if (self._clock() - started) * 1000 > self._limits.timeout_ms:
            return self._error("plan_timeout")
        return None

    @staticmethod
    def _skip_pending(states: dict[str, StepExecutionState]) -> None:
        for step_id, state in tuple(states.items()):
            if state.status == "pending":
                states[step_id] = StepExecutionState(
                    step_id=step_id,
                    kind=state.kind,
                    status="skipped",
                    attempts=0,
                )

    @staticmethod
    def _error(code: str) -> PlanningError:
        return PlanningError(code=code, message=SAFE_MESSAGES[code])

    @staticmethod
    def _failed(plan, state, history, error, provider_calls):
        return PlanExecutionResult(
            status="failed",
            plan=plan,
            state=state,
            state_history=tuple(history),
            output=None,
            error=error,
            provider_calls=provider_calls,
        )


class _StepFailure(Exception):
    def __init__(self, error: PlanningError):
        super().__init__(error.code)
        self.error = error
