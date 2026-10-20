"""Durable execution adapter for a validated controlled Planner.

This layer intentionally reuses the Phase 5.3 store/recovery simulator and the
Phase 5.4 structured provider. It adds no schema, public route, or live-provider
composition.
"""

from __future__ import annotations

from datetime import datetime
import json
import os
from uuid import UUID

from pydantic import Field, ValidationError

from backend.tools.models import StrictModel, ToolExecutionContext

from .checkpoint_models import ExecutionCreation, SimulationResult
from .checkpoint_recovery import CrashPoint, CrashRecoverySimulator, initial_agent_state
from .checkpoint_store import (
    CheckpointStoreError,
    PostgresCheckpointStore,
    canonical_json_sha256,
)
from .executor import BoundedPlanExecutor
from .models import Plan, PlanningRequest, SalesGrowthComparison
from .structured_provider import PlannerProviderError
from .validation import PlanValidator


PERSISTENT_PLANNER_POLICY_VERSION = "bounded-plan-policy-v1"
PERSISTENT_PLANNER_RUNTIME_VERSION = "durable-controlled-planner-v1"


class DurableControlledPlannerSettings(StrictModel):
    enabled: bool = False
    lease_ttl_seconds: int = Field(default=10, ge=1, le=300)


class DurablePlannerError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def load_durable_controlled_planner_settings() -> DurableControlledPlannerSettings:
    raw = os.getenv("DURABLE_LLM_PLANNER_ENABLED", "false").strip().lower()
    values = {"true": True, "false": False, "1": True, "0": False}
    if raw not in values:
        raise ValueError("Invalid DURABLE_LLM_PLANNER_ENABLED value")
    return DurableControlledPlannerSettings(enabled=values[raw])


class DurableControlledPlanner:
    """Persist a validated generated Plan, then execute one durable step at a time."""

    def __init__(
        self,
        provider,
        store: PostgresCheckpointStore,
        tool_executor,
        *,
        settings: DurableControlledPlannerSettings | None = None,
    ):
        self.settings = settings or load_durable_controlled_planner_settings()
        if not self.settings.enabled:
            raise ValueError("durable_llm_planner_disabled")
        self._provider = provider
        self._store = store
        self._tool_executor = tool_executor
        self._bounded_executor = BoundedPlanExecutor(tool_executor)
        self._validator = PlanValidator()
        self._recovery = CrashRecoverySimulator(
            store,
            lease_ttl_seconds=self.settings.lease_ttl_seconds,
        )

    def submit(
        self,
        request: PlanningRequest,
        *,
        scope_key: str,
        idempotency_key: str,
        now: datetime,
    ) -> ExecutionCreation:
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("durable planner time must be timezone-aware")
        request_sha = canonical_json_sha256(request.model_dump(mode="json"))
        try:
            existing = self._store.find_execution_by_idempotency(
                scope_key=scope_key,
                idempotency_key=idempotency_key,
                request_sha256=request_sha,
            )
        except CheckpointStoreError as error:
            raise DurablePlannerError(error.code) from None
        if existing is not None:
            return existing

        try:
            raw = self._provider.propose(request)
            plan = Plan.model_validate_json(raw, strict=True)
        except PlannerProviderError:
            raise DurablePlannerError("durable_planner_provider_error") from None
        except (ValidationError, ValueError, TypeError, UnicodeError):
            raise DurablePlannerError("durable_planner_plan_invalid") from None
        except Exception:
            raise DurablePlannerError("durable_planner_provider_error") from None
        if (
            plan.task != request.task
            or plan.baseline_period != request.baseline_period
            or plan.comparison_period != request.comparison_period
            or plan.region != request.region
        ):
            raise DurablePlannerError("durable_planner_request_mismatch")
        validation = self._validator.validate(plan)
        if validation.status != "valid":
            raise DurablePlannerError(validation.error.code)
        try:
            return self._store.create_or_get_execution(
                scope_key=scope_key,
                idempotency_key=idempotency_key,
                request_sha256=request_sha,
                plan=plan,
                initial_state=initial_agent_state(plan),
                policy_version=PERSISTENT_PLANNER_POLICY_VERSION,
                runtime_version=PERSISTENT_PLANNER_RUNTIME_VERSION,
                now=now,
            )
        except CheckpointStoreError as error:
            raise DurablePlannerError(error.code) from None

    def run_next(
        self,
        execution_id: UUID,
        *,
        worker_id: str,
        now: datetime,
        crash_at: CrashPoint | None = None,
    ) -> SimulationResult:
        return self._recovery.run_next(
            execution_id,
            worker_id=worker_id,
            now=now,
            operation=lambda step: self._execute_step(execution_id, step),
            crash_at=crash_at,
        )

    def final_output(self, execution_id: UUID) -> SalesGrowthComparison | None:
        execution = self._store.get_execution(execution_id)
        if execution.status != "completed":
            return None
        checkpoint = self._store.get_latest_checkpoint(execution_id)
        output = checkpoint.agent_state.step_states[-1].output
        return SalesGrowthComparison.model_validate_json(
            json.dumps(output, ensure_ascii=False), strict=True
        )

    def _execute_step(self, execution_id: UUID, step) -> dict:
        if step.kind == "tool":
            result = self._tool_executor.execute(
                step.tool_call,
                ToolExecutionContext(
                    principal="analytics-copilot",
                    correlation_id=f"durable-{execution_id}-{step.step_id}",
                ),
            )
            if result.status != "success" or result.output is None:
                raise DurablePlannerError(
                    result.error.code if result.error else "tool_execution_failed"
                )
            return result.output

        checkpoint = self._store.get_latest_checkpoint(execution_id)
        states = {state.step_id: state for state in checkpoint.agent_state.step_states}
        output = self._bounded_executor.compute_growth(step.computation, states)
        return SalesGrowthComparison.model_validate(output, strict=True).model_dump(
            mode="json"
        )
