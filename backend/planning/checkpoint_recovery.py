"""Deterministic crash/recovery simulator for durable planning checkpoints."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Literal
from uuid import UUID

from .checkpoint_models import RecoveryDecision, SimulationResult
from .checkpoint_store import LeaseUnavailable, PostgresCheckpointStore
from .models import AgentState, Plan, PlanStep, StepExecutionState
from .transitions import validate_agent_transition


CrashPoint = Literal["after_start", "after_operation", "after_checkpoint"]
StepOperation = Callable[[PlanStep], dict]


def initial_agent_state(plan: Plan) -> AgentState:
    return AgentState(
        plan_id=plan.plan_id,
        status="pending",
        current_step_id=None,
        step_states=tuple(
            StepExecutionState(
                step_id=step.step_id,
                kind=step.kind,
                status="pending",
                attempts=0,
            )
            for step in plan.steps
        ),
        completed_step_ids=(),
        steps_executed=0,
        tool_calls_used=0,
        budget_units_used=0,
        elapsed_ms=0,
        error=None,
    )


class CrashRecoverySimulator:
    """Runs one durable step at a time with explicit crash injection points."""

    def __init__(self, store: PostgresCheckpointStore, *, lease_ttl_seconds: int = 10):
        if not 1 <= lease_ttl_seconds <= 300:
            raise ValueError("invalid simulator lease TTL")
        self._store = store
        self._lease_ttl_seconds = lease_ttl_seconds

    def recover(
        self,
        execution_id: UUID,
        *,
        worker_id: str,
        now: datetime,
    ) -> RecoveryDecision:
        execution = self._store.get_execution(execution_id)
        if execution.status in {"completed", "failed"}:
            return RecoveryDecision(
                outcome="terminal",
                execution_id=execution_id,
                state_version=execution.state_version,
                next_step_id=None,
                reason="execution_terminal",
            )
        if execution.status == "reconciling":
            return RecoveryDecision(
                outcome="reconciliation_required",
                execution_id=execution_id,
                state_version=execution.state_version,
                next_step_id=None,
                reason="ambiguous_step",
            )
        try:
            execution = self._store.acquire_lease(
                execution_id,
                worker_id=worker_id,
                now=now,
                ttl_seconds=self._lease_ttl_seconds,
            )
        except LeaseUnavailable:
            return RecoveryDecision(
                outcome="lease_unavailable",
                execution_id=execution_id,
                state_version=execution.state_version,
                next_step_id=None,
                reason="active_lease",
            )

        checkpoint = self._store.get_latest_checkpoint(execution_id)
        receipts = self._store.list_receipts(execution_id)
        started = next((item for item in receipts if item.status == "started"), None)
        if started is not None:
            updated, _, _ = self._store.mark_step_ambiguous(
                execution_id,
                worker_id=worker_id,
                expected_state_version=execution.state_version,
                step_id=started.step_id,
                now=now,
            )
            return RecoveryDecision(
                outcome="reconciliation_required",
                execution_id=execution_id,
                state_version=updated.state_version,
                next_step_id=None,
                reason="ambiguous_step",
            )
        if any(item.status == "ambiguous" for item in receipts):
            self._store.release_lease(
                execution_id, worker_id=worker_id, now=now
            )
            return RecoveryDecision(
                outcome="reconciliation_required",
                execution_id=execution_id,
                state_version=execution.state_version,
                next_step_id=None,
                reason="ambiguous_step",
            )
        if any(step.status == "running" for step in checkpoint.agent_state.step_states):
            updated, _ = self._store.save_checkpoint(
                execution_id,
                worker_id=worker_id,
                expected_state_version=execution.state_version,
                execution_status="reconciling",
                agent_state=checkpoint.agent_state,
                now=now,
            )
            return RecoveryDecision(
                outcome="reconciliation_required",
                execution_id=execution_id,
                state_version=updated.state_version,
                next_step_id=None,
                reason="inconsistent_checkpoint",
            )

        completed = set(checkpoint.agent_state.completed_step_ids)
        next_step = next(
            (
                step
                for step in execution.plan.steps
                if step.step_id not in completed
                and all(dependency in completed for dependency in step.depends_on)
            ),
            None,
        )
        if next_step is None:
            updated, _ = self._store.save_checkpoint(
                execution_id,
                worker_id=worker_id,
                expected_state_version=execution.state_version,
                execution_status="reconciling",
                agent_state=checkpoint.agent_state,
                now=now,
            )
            return RecoveryDecision(
                outcome="reconciliation_required",
                execution_id=execution_id,
                state_version=updated.state_version,
                next_step_id=None,
                reason="inconsistent_checkpoint",
            )
        return RecoveryDecision(
            outcome="resumable",
            execution_id=execution_id,
            state_version=execution.state_version,
            next_step_id=next_step.step_id,
            reason="dependency_ready",
        )

    def run_next(
        self,
        execution_id: UUID,
        *,
        worker_id: str,
        now: datetime,
        operation: StepOperation,
        crash_at: CrashPoint | None = None,
    ) -> SimulationResult:
        decision = self.recover(execution_id, worker_id=worker_id, now=now)
        if decision.outcome != "resumable":
            outcome = {
                "terminal": "terminal",
                "reconciliation_required": "reconciliation_required",
                "lease_unavailable": "lease_unavailable",
            }[decision.outcome]
            return SimulationResult(
                outcome=outcome,
                execution_id=execution_id,
                step_id=None,
                operation_calls=0,
                state_version=decision.state_version,
            )

        execution = self._store.get_execution(execution_id)
        checkpoint = self._store.get_latest_checkpoint(execution_id)
        step = next(
            item for item in execution.plan.steps if item.step_id == decision.next_step_id
        )
        action_name, input_payload = self._step_action(step)
        token = self._store.derive_step_token(
            execution_id, execution.plan_sha256, step.step_id
        )
        running_state = self._running_state(checkpoint.agent_state, step)
        started = self._store.start_step(
            execution_id,
            worker_id=worker_id,
            expected_state_version=decision.state_version,
            running_state=running_state,
            step_id=step.step_id,
            action_name=action_name,
            input_payload=input_payload,
            idempotency_token=token,
            now=now,
        )
        if started.deduplicated:
            self._store.release_lease(
                execution_id, worker_id=worker_id, now=now
            )
            return SimulationResult(
                outcome="step_completed",
                execution_id=execution_id,
                step_id=step.step_id,
                operation_calls=0,
                state_version=started.execution.state_version,
            )
        if crash_at == "after_start":
            return SimulationResult(
                outcome="crashed",
                execution_id=execution_id,
                step_id=step.step_id,
                operation_calls=0,
                state_version=started.execution.state_version,
            )

        try:
            output = operation(step)
            if not isinstance(output, dict):
                raise TypeError("simulated step output must be an object")
        except Exception:
            updated, _, _ = self._store.mark_step_ambiguous(
                execution_id,
                worker_id=worker_id,
                expected_state_version=started.execution.state_version,
                step_id=step.step_id,
                now=now + timedelta(milliseconds=1),
            )
            return SimulationResult(
                outcome="reconciliation_required",
                execution_id=execution_id,
                step_id=step.step_id,
                operation_calls=1,
                state_version=updated.state_version,
            )
        if crash_at == "after_operation":
            return SimulationResult(
                outcome="crashed",
                execution_id=execution_id,
                step_id=step.step_id,
                operation_calls=1,
                state_version=started.execution.state_version,
            )

        completed_state = self._completed_state(running_state, step, output, token)
        updated, _, _ = self._store.complete_step(
            execution_id,
            worker_id=worker_id,
            expected_state_version=started.execution.state_version,
            completed_state=completed_state,
            step_id=step.step_id,
            idempotency_token=token,
            output=output,
            tool_call_id=(f"sim-{token[:16]}" if step.kind == "tool" else None),
            now=now + timedelta(milliseconds=1),
        )
        if crash_at == "after_checkpoint":
            return SimulationResult(
                outcome="crashed",
                execution_id=execution_id,
                step_id=step.step_id,
                operation_calls=1,
                state_version=updated.state_version,
            )
        if updated.status != "completed":
            self._store.release_lease(
                execution_id,
                worker_id=worker_id,
                now=now + timedelta(milliseconds=2),
            )
        return SimulationResult(
            outcome=(
                "execution_completed"
                if updated.status == "completed"
                else "step_completed"
            ),
            execution_id=execution_id,
            step_id=step.step_id,
            operation_calls=1,
            state_version=updated.state_version,
        )

    @staticmethod
    def _step_action(step: PlanStep) -> tuple[str, dict]:
        if step.kind == "tool":
            return step.tool_call.name, dict(step.tool_call.arguments)
        return step.computation.operation, step.computation.model_dump(mode="json")

    @staticmethod
    def _running_state(state: AgentState, step: PlanStep) -> AgentState:
        updated_steps = tuple(
            StepExecutionState(
                step_id=item.step_id,
                kind=item.kind,
                status="running",
                attempts=1,
            )
            if item.step_id == step.step_id
            else item
            for item in state.step_states
        )
        updated = AgentState(
            plan_id=state.plan_id,
            status="running",
            current_step_id=step.step_id,
            step_states=updated_steps,
            completed_step_ids=state.completed_step_ids,
            steps_executed=state.steps_executed,
            tool_calls_used=state.tool_calls_used + (1 if step.kind == "tool" else 0),
            budget_units_used=state.budget_units_used + 1,
            elapsed_ms=state.elapsed_ms + 1,
            error=None,
        )
        validate_agent_transition(state, updated)
        return updated

    @staticmethod
    def _completed_state(
        state: AgentState,
        step: PlanStep,
        output: dict,
        token: str,
    ) -> AgentState:
        updated_steps = tuple(
            StepExecutionState(
                step_id=item.step_id,
                kind=item.kind,
                status="completed",
                attempts=1,
                output=output,
                latency_ms=1,
                tool_call_id=(f"sim-{token[:16]}" if step.kind == "tool" else None),
            )
            if item.step_id == step.step_id
            else item
            for item in state.step_states
        )
        completed_ids = (*state.completed_step_ids, step.step_id)
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
