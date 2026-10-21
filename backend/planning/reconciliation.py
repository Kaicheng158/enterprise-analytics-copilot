"""Evidence-bound reconciliation for externally ambiguous tool outcomes."""

from datetime import datetime
import json
import re
from uuid import UUID

from pydantic import Field, field_validator

from backend.tools.models import StrictModel
from backend.tools.registry import ToolRegistry, ToolRegistryError

from .checkpoint_models import ReconciliationResolution
from .checkpoint_store import CheckpointConflict, PostgresCheckpointStore
from .models import AgentState, PlanningError, StepExecutionState
from .transitions import validate_agent_transition


class ReconciliationPolicy(StrictModel):
    allowed_actors: frozenset[str] = Field(min_length=1)

    @field_validator("allowed_actors")
    @classmethod
    def actors_are_server_identities(cls, value):
        if any(not re.fullmatch(r"[a-z][a-z0-9_-]{0,127}", actor) for actor in value):
            raise ValueError("invalid reconciliation actor")
        return value


class ReconciliationAuthorizationError(RuntimeError):
    pass


class ReconciliationService:
    def __init__(
        self,
        store: PostgresCheckpointStore,
        policy: ReconciliationPolicy,
        tool_registry: ToolRegistry,
        *,
        tool_principal: str = "analytics-copilot",
    ):
        self._store = store
        self._policy = policy
        self._tool_registry = tool_registry
        self._tool_principal = tool_principal

    def confirm_succeeded(
        self,
        execution_id: UUID,
        *,
        step_id: str,
        expected_state_version: int,
        output: dict,
        tool_call_id: str | None,
        evidence_sha256: str,
        actor_id: str,
        now: datetime,
    ) -> ReconciliationResolution:
        self._authorize(actor_id)
        execution = self._store.get_execution(execution_id)
        checkpoint = self._store.get_latest_checkpoint(execution_id)
        if execution.state_version != expected_state_version:
            raise CheckpointConflict("checkpoint_cas_conflict")
        case = self._store.get_reconciliation_case(execution_id)
        if case.step_id != step_id:
            raise CheckpointConflict("reconciliation_step_mismatch")
        step = next(item for item in execution.plan.steps if item.step_id == step_id)
        if step.kind == "tool":
            if not tool_call_id:
                raise ValueError("verified tool outcome requires tool_call_id")
            try:
                definition = self._tool_registry.resolve(
                    step.tool_call.name, self._tool_principal
                )
                # Tool receipts persist the executor's JSON representation.  Validate
                # that JSON contract directly so strict types such as Decimal can be
                # reconstructed without weakening validation for Python objects.
                output = definition.output_model.model_validate_json(
                    json.dumps(output, ensure_ascii=False), strict=True
                ).model_dump(mode="json")
            except (ToolRegistryError, ValueError, TypeError):
                raise ValueError("verified tool output is invalid or unauthorized") from None
        resolved_state = self._succeeded_state(
            checkpoint.agent_state, step_id, output, tool_call_id
        )
        return self._store.resolve_ambiguous_step(
            execution_id,
            expected_state_version=expected_state_version,
            step_id=step_id,
            decision="confirmed_succeeded",
            resolved_state=resolved_state,
            evidence_sha256=evidence_sha256,
            actor_id=actor_id,
            now=now,
            output=output,
            tool_call_id=tool_call_id,
        )

    def confirm_failed(
        self,
        execution_id: UUID,
        *,
        step_id: str,
        expected_state_version: int,
        evidence_sha256: str,
        actor_id: str,
        now: datetime,
    ) -> ReconciliationResolution:
        self._authorize(actor_id)
        execution = self._store.get_execution(execution_id)
        checkpoint = self._store.get_latest_checkpoint(execution_id)
        if execution.state_version != expected_state_version:
            raise CheckpointConflict("checkpoint_cas_conflict")
        case = self._store.get_reconciliation_case(execution_id)
        if case.step_id != step_id:
            raise CheckpointConflict("reconciliation_step_mismatch")
        error = PlanningError(
            code="tool_execution_failed",
            message="Verified external operation did not complete successfully",
        )
        resolved_state = self._failed_state(checkpoint.agent_state, step_id, error)
        return self._store.resolve_ambiguous_step(
            execution_id,
            expected_state_version=expected_state_version,
            step_id=step_id,
            decision="confirmed_failed",
            resolved_state=resolved_state,
            evidence_sha256=evidence_sha256,
            actor_id=actor_id,
            now=now,
            error_code="verified_external_failure",
        )

    def _authorize(self, actor_id: str) -> None:
        if actor_id not in self._policy.allowed_actors:
            raise ReconciliationAuthorizationError("reconciliation_actor_not_authorized")

    @staticmethod
    def _succeeded_state(state: AgentState, step_id: str, output: dict, tool_call_id: str | None) -> AgentState:
        updated_steps = tuple(
            StepExecutionState(
                step_id=item.step_id,
                kind=item.kind,
                status="completed",
                attempts=item.attempts,
                output=output,
                latency_ms=item.latency_ms,
                tool_call_id=tool_call_id if item.kind == "tool" else None,
            )
            if item.step_id == step_id
            else item
            for item in state.step_states
        )
        completed_ids = (*state.completed_step_ids, step_id)
        terminal = len(completed_ids) == len(updated_steps)
        updated = AgentState(
            plan_id=state.plan_id,
            status="completed" if terminal else "running",
            current_step_id=None,
            step_states=updated_steps,
            completed_step_ids=completed_ids,
            steps_executed=len(completed_ids),
            tool_calls_used=state.tool_calls_used,
            budget_units_used=state.budget_units_used,
            elapsed_ms=state.elapsed_ms + 1,
            error=None,
        )
        validate_agent_transition(state, updated)
        return updated

    @staticmethod
    def _failed_state(state: AgentState, step_id: str, error: PlanningError) -> AgentState:
        updated_steps = tuple(
            StepExecutionState(
                step_id=item.step_id,
                kind=item.kind,
                status="failed",
                attempts=item.attempts,
                error=error,
                latency_ms=item.latency_ms,
                tool_call_id=item.tool_call_id,
            )
            if item.step_id == step_id
            else item
            for item in state.step_states
        )
        updated = AgentState(
            plan_id=state.plan_id,
            status="failed",
            current_step_id=None,
            step_states=updated_steps,
            completed_step_ids=state.completed_step_ids,
            steps_executed=state.steps_executed,
            tool_calls_used=state.tool_calls_used,
            budget_units_used=state.budget_units_used,
            elapsed_ms=state.elapsed_ms + 1,
            error=error,
        )
        validate_agent_transition(state, updated)
        return updated
