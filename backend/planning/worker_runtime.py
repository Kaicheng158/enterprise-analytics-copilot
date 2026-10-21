"""Default-off durable worker with timed heartbeats and lease fencing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
from threading import Event, Thread
from time import monotonic
from typing import Callable, Literal
from uuid import UUID

from pydantic import Field, model_validator

from backend.tools.models import StrictModel

from .checkpoint_models import LeaseSession
from .checkpoint_recovery import CrashRecoverySimulator
from .checkpoint_store import PostgresCheckpointStore
from .worker_coordination import WorkerLeaseCoordinator


WorkerCrashPoint = Literal["after_start", "after_operation", "after_checkpoint"]
StepOperation = Callable[[object], dict]


class DurableWorkerSettings(StrictModel):
    enabled: bool = False
    lease_ttl_seconds: int = Field(default=10, ge=1, le=300)
    heartbeat_interval_ms: int = Field(default=1_000, ge=25, le=60_000)
    operation_timeout_ms: int = Field(default=30_000, ge=100, le=300_000)

    @model_validator(mode="after")
    def heartbeat_precedes_expiry(self):
        if self.heartbeat_interval_ms >= self.lease_ttl_seconds * 1_000:
            raise ValueError("heartbeat interval must be shorter than lease TTL")
        return self


class WorkerRunResult(StrictModel):
    outcome: Literal[
        "step_completed",
        "execution_completed",
        "lease_lost",
        "reconciliation_required",
        "terminal",
        "lease_unavailable",
        "crashed",
    ]
    execution_id: UUID
    step_id: str | None = None
    operation_calls: int = Field(ge=0, le=1)
    heartbeat_count: int = Field(ge=0)
    state_version: int = Field(ge=0)


def load_durable_worker_settings() -> DurableWorkerSettings:
    raw = os.getenv("DURABLE_WORKER_RUNTIME_ENABLED", "false").strip().lower()
    values = {"true": True, "false": False, "1": True, "0": False}
    if raw not in values:
        raise ValueError("Invalid DURABLE_WORKER_RUNTIME_ENABLED value")
    return DurableWorkerSettings(enabled=values[raw])


class DurableWorkerRuntime:
    """Run one durable step while a daemon heartbeat fences database writes."""

    def __init__(
        self,
        store: PostgresCheckpointStore,
        *,
        settings: DurableWorkerSettings | None = None,
        now_factory: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] = monotonic,
    ):
        self.settings = settings or load_durable_worker_settings()
        if not self.settings.enabled:
            raise ValueError("durable_worker_runtime_disabled")
        self._store = store
        self._now = now_factory or (lambda: datetime.now(UTC))
        self._monotonic = monotonic_clock
        self._recovery = CrashRecoverySimulator(
            store, lease_ttl_seconds=self.settings.lease_ttl_seconds
        )
        self._leases = WorkerLeaseCoordinator(
            store, ttl_seconds=self.settings.lease_ttl_seconds
        )

    def run_next(
        self,
        execution_id: UUID,
        *,
        worker_id: str,
        operation: StepOperation,
        crash_at: WorkerCrashPoint | None = None,
    ) -> WorkerRunResult:
        now = self._now()
        decision = self._recovery.recover(
            execution_id, worker_id=worker_id, now=now
        )
        if decision.outcome != "resumable":
            return WorkerRunResult(
                outcome={
                    "terminal": "terminal",
                    "reconciliation_required": "reconciliation_required",
                    "lease_unavailable": "lease_unavailable",
                }[decision.outcome],
                execution_id=execution_id,
                operation_calls=0,
                heartbeat_count=0,
                state_version=decision.state_version,
            )

        execution = self._store.get_execution(execution_id)
        checkpoint = self._store.get_latest_checkpoint(execution_id)
        step = next(
            item for item in execution.plan.steps if item.step_id == decision.next_step_id
        )
        action_name, input_payload = self._recovery._step_action(step)
        token = self._store.derive_step_token(
            execution_id, execution.plan_sha256, step.step_id
        )
        running_state = self._recovery._running_state(checkpoint.agent_state, step)
        try:
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
        except Exception:
            return self._lost_or_reconciling(
                execution_id, step.step_id, 0, 0
            )
        if started.deduplicated:
            self._safe_release(execution_id, worker_id)
            return WorkerRunResult(
                outcome="step_completed",
                execution_id=execution_id,
                step_id=step.step_id,
                operation_calls=0,
                heartbeat_count=0,
                state_version=started.execution.state_version,
            )
        session = LeaseSession(
            execution_id=execution_id,
            worker_id=worker_id,
            state_version=started.execution.state_version,
            lease_expires_at=started.execution.lease_expires_at,
        )
        if crash_at == "after_start":
            return WorkerRunResult(
                outcome="crashed",
                execution_id=execution_id,
                step_id=step.step_id,
                operation_calls=0,
                heartbeat_count=0,
                state_version=session.state_version,
            )

        done = Event()
        result: dict[str, object] = {}

        def invoke() -> None:
            try:
                result["output"] = operation(step)
            except BaseException as error:  # kept in-process and never logged verbatim
                result["error"] = error
            finally:
                done.set()

        Thread(target=invoke, name=f"durable-step-{step.step_id}", daemon=True).start()
        heartbeat_count = 0
        deadline = self._monotonic() + self.settings.operation_timeout_ms / 1_000
        interval = self.settings.heartbeat_interval_ms / 1_000
        while not done.is_set():
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return self._mark_unknown(
                    execution_id,
                    step.step_id,
                    worker_id,
                    session.state_version,
                    heartbeat_count,
                )
            if done.wait(min(interval, remaining)):
                break
            try:
                session = self._leases.heartbeat(session, now=self._now())
                heartbeat_count += 1
            except Exception:
                return self._lost_or_reconciling(
                    execution_id, step.step_id, 1, heartbeat_count
                )

        if crash_at == "after_operation":
            return WorkerRunResult(
                outcome="crashed",
                execution_id=execution_id,
                step_id=step.step_id,
                operation_calls=1,
                heartbeat_count=heartbeat_count,
                state_version=session.state_version,
            )
        try:
            self._leases.assert_current(session, now=self._now())
        except Exception:
            return self._lost_or_reconciling(
                execution_id, step.step_id, 1, heartbeat_count
            )
        output = result.get("output")
        if "error" in result or not isinstance(output, dict):
            return self._mark_unknown(
                execution_id,
                step.step_id,
                worker_id,
                session.state_version,
                heartbeat_count,
            )
        completed_state = self._recovery._completed_state(
            running_state, step, output, token
        )
        try:
            updated, _, _ = self._store.complete_step(
                execution_id,
                worker_id=worker_id,
                expected_state_version=session.state_version,
                completed_state=completed_state,
                step_id=step.step_id,
                idempotency_token=token,
                output=output,
                tool_call_id=(f"worker-{token[:16]}" if step.kind == "tool" else None),
                now=self._now(),
            )
        except Exception:
            return self._lost_or_reconciling(
                execution_id, step.step_id, 1, heartbeat_count
            )
        if crash_at == "after_checkpoint":
            return WorkerRunResult(
                outcome="crashed",
                execution_id=execution_id,
                step_id=step.step_id,
                operation_calls=1,
                heartbeat_count=heartbeat_count,
                state_version=updated.state_version,
            )
        if updated.status != "completed":
            self._safe_release(execution_id, worker_id)
        return WorkerRunResult(
            outcome=(
                "execution_completed" if updated.status == "completed" else "step_completed"
            ),
            execution_id=execution_id,
            step_id=step.step_id,
            operation_calls=1,
            heartbeat_count=heartbeat_count,
            state_version=updated.state_version,
        )

    def _mark_unknown(
        self,
        execution_id: UUID,
        step_id: str,
        worker_id: str,
        state_version: int,
        heartbeat_count: int,
    ) -> WorkerRunResult:
        try:
            updated, _, _ = self._store.mark_step_ambiguous(
                execution_id,
                worker_id=worker_id,
                expected_state_version=state_version,
                step_id=step_id,
                now=self._now(),
            )
            return WorkerRunResult(
                outcome="reconciliation_required",
                execution_id=execution_id,
                step_id=step_id,
                operation_calls=1,
                heartbeat_count=heartbeat_count,
                state_version=updated.state_version,
            )
        except Exception:
            return self._lost_or_reconciling(
                execution_id, step_id, 1, heartbeat_count
            )

    def _lost_or_reconciling(
        self,
        execution_id: UUID,
        step_id: str | None,
        operation_calls: int,
        heartbeat_count: int,
    ) -> WorkerRunResult:
        try:
            execution = self._store.get_execution(execution_id)
            outcome = (
                "reconciliation_required"
                if execution.status == "reconciling"
                else "lease_lost"
            )
            state_version = execution.state_version
        except Exception:
            # A database outage must stop this worker, never authorize a write.
            outcome = "lease_lost"
            state_version = 0
        return WorkerRunResult(
            outcome=outcome,
            execution_id=execution_id,
            step_id=step_id,
            operation_calls=operation_calls,
            heartbeat_count=heartbeat_count,
            state_version=state_version,
        )

    def _safe_release(self, execution_id: UUID, worker_id: str) -> None:
        try:
            self._store.release_lease(
                execution_id, worker_id=worker_id, now=self._now()
            )
        except Exception:
            pass
