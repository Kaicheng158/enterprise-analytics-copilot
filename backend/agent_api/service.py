"""Adapter from the internal Agent API to the existing durable runtime/store."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Callable, Protocol
from uuid import UUID

import psycopg
from pydantic import ValidationError

from backend.planning import DurableAgentRuntime, PlanningRequest, SalesGrowthComparison
from backend.planning.checkpoint_models import ExecutionCreation, PersistentCheckpoint, PersistentExecution
from backend.planning.checkpoint_store import (
    CheckpointStoreError,
    ExecutionNotFound,
    PostgresCheckpointStore,
)
from backend.planning.durable_controlled_runtime import DurablePlannerError

from .models import (
    AgentCaller,
    AgentTaskError,
    AgentTaskProgress,
    AgentTaskRequest,
    AgentTaskResponse,
)


class AgentTaskController(Protocol):
    def submit(
        self,
        request: AgentTaskRequest,
        *,
        idempotency_key: str,
        caller: AgentCaller,
    ) -> AgentTaskResponse: ...

    def get(self, task_id: UUID, *, caller: AgentCaller) -> AgentTaskResponse: ...


class AgentTaskServiceError(RuntimeError):
    def __init__(self, *, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class UnavailableAgentTaskController:
    def submit(self, request, *, idempotency_key, caller):
        raise AgentTaskServiceError(
            status_code=503,
            code="agent_runtime_unavailable",
            message="Internal Agent runtime is not configured",
        )

    def get(self, task_id, *, caller):
        raise AgentTaskServiceError(
            status_code=503,
            code="agent_runtime_unavailable",
            message="Internal Agent runtime is not configured",
        )


class DurableAgentTaskController:
    """Thin API adapter; execution remains in the existing durable modules."""

    def __init__(
        self,
        agent: DurableAgentRuntime,
        store: PostgresCheckpointStore,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self._agent = agent
        self._store = store
        self._clock = clock

    def submit(
        self,
        request: AgentTaskRequest,
        *,
        idempotency_key: str,
        caller: AgentCaller,
    ) -> AgentTaskResponse:
        self._require_caller(caller)
        planning_request = PlanningRequest.model_validate(
            request.model_dump(mode="json"), strict=True
        )
        try:
            creation = self._agent.submit(
                planning_request,
                scope_key=caller.scope_key,
                idempotency_key=idempotency_key,
                now=self._clock(),
            )
        except DurablePlannerError as error:
            if error.code == "idempotency_key_conflict":
                raise AgentTaskServiceError(
                    status_code=409,
                    code="agent_idempotency_conflict",
                    message="Idempotency key is already bound to another request",
                ) from None
            if error.code in {
                "durable_planner_provider_error",
                "durable_planner_plan_invalid",
                "durable_planner_request_mismatch",
                "action_not_authorized",
                "step_limit_exceeded",
                "tool_call_limit_exceeded",
                "budget_limit_exceeded",
            }:
                raise AgentTaskServiceError(
                    status_code=502,
                    code="agent_plan_rejected",
                    message="Planner did not produce an executable authorized plan",
                ) from None
            raise AgentTaskServiceError(
                status_code=503,
                code="agent_persistence_unavailable",
                message="Agent task could not be persisted",
            ) from None
        except psycopg.Error:
            raise AgentTaskServiceError(
                status_code=503,
                code="agent_persistence_unavailable",
                message="Agent task could not be persisted",
            ) from None
        return self._response(creation.execution, creation.checkpoint, creation.created)

    def get(self, task_id: UUID, *, caller: AgentCaller) -> AgentTaskResponse:
        self._require_caller(caller)
        try:
            execution = self._store.get_execution(task_id)
            if execution.scope_key != caller.scope_key:
                raise ExecutionNotFound("execution_not_found")
            checkpoint = self._store.get_latest_checkpoint(task_id)
        except ExecutionNotFound:
            raise AgentTaskServiceError(
                status_code=404,
                code="agent_task_not_found",
                message="Agent task was not found",
            ) from None
        except CheckpointStoreError:
            raise AgentTaskServiceError(
                status_code=503,
                code="agent_persistence_unavailable",
                message="Agent task status is unavailable",
            ) from None
        except psycopg.Error:
            raise AgentTaskServiceError(
                status_code=503,
                code="agent_persistence_unavailable",
                message="Agent task status is unavailable",
            ) from None
        return self._response(execution, checkpoint, None)

    @staticmethod
    def _require_caller(caller: AgentCaller) -> None:
        if caller != AgentCaller():
            raise AgentTaskServiceError(
                status_code=403,
                code="agent_forbidden",
                message="Agent caller is not authorized for this scope",
            )

    @staticmethod
    def _response(
        execution: PersistentExecution,
        checkpoint: PersistentCheckpoint,
        created: bool | None,
    ) -> AgentTaskResponse:
        if (
            checkpoint.execution_id != execution.execution_id
            or checkpoint.agent_state.plan_id != execution.plan.plan_id
        ):
            raise AgentTaskServiceError(
                status_code=503,
                code="agent_state_integrity_error",
                message="Agent task state failed integrity validation",
            )
        if (
            checkpoint.state_version != execution.state_version
            or checkpoint.execution_status != execution.status
        ):
            raise AgentTaskServiceError(
                status_code=409,
                code="agent_status_changed",
                message="Agent task changed while status was read; retry the request",
            )
        status = {
            "pending": "queued",
            "running": "running",
            "reconciling": "needs_reconciliation",
            "completed": "completed",
            "failed": "failed",
        }[execution.status]
        state = checkpoint.agent_state
        result = None
        error = None
        if status == "completed":
            try:
                result = SalesGrowthComparison.model_validate_json(
                    json.dumps(state.step_states[-1].output, ensure_ascii=False),
                    strict=True,
                )
            except (ValidationError, TypeError):
                raise AgentTaskServiceError(
                    status_code=503,
                    code="agent_state_integrity_error",
                    message="Agent task state failed integrity validation",
                ) from None
        elif status == "needs_reconciliation":
            error = AgentTaskError(
                code="agent_reconciliation_required",
                message="Task outcome requires authorized reconciliation",
            )
        elif status == "failed":
            error = AgentTaskError(
                code="agent_execution_failed",
                message="Agent task execution failed",
            )
        request = AgentTaskRequest(
            task=execution.plan.task,
            baseline_period=execution.plan.baseline_period,
            comparison_period=execution.plan.comparison_period,
            region=execution.plan.region,
        )
        return AgentTaskResponse(
            task_id=execution.execution_id,
            status=status,
            created=created,
            request=request,
            progress=AgentTaskProgress(
                completed_steps=len(state.completed_step_ids),
                total_steps=len(state.step_states),
            ),
            result=result,
            error=error,
            state_version=execution.state_version,
            policy_version=execution.policy_version,
            runtime_version=execution.runtime_version,
            updated_at=execution.updated_at,
        )
