"""Default-off end-to-end composition of durable planning and worker execution."""

from datetime import datetime
import os
import re
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from backend.tools.models import StrictModel

from .checkpoint_models import ExecutionCreation
from .durable_controlled_runtime import DurableControlledPlanner
from .models import PlanningRequest, SalesGrowthComparison
from .worker_runtime import DurableWorkerRuntime, WorkerCrashPoint, WorkerRunResult


class DurableAgentSettings(StrictModel):
    enabled: bool = False
    max_steps_per_run: int = Field(default=8, ge=1, le=8)


class DurableAgentResult(StrictModel):
    execution_id: UUID
    status: Literal["completed", "reconciling", "paused", "failed", "incomplete"]
    worker_outcomes: tuple[WorkerRunResult, ...]
    output: SalesGrowthComparison | None = None

    @model_validator(mode="after")
    def output_matches_status(self):
        if (self.status == "completed") != (self.output is not None):
            raise ValueError("only completed durable agents return final output")
        return self


def load_durable_agent_settings() -> DurableAgentSettings:
    raw = os.getenv("DURABLE_AGENT_RUNTIME_ENABLED", "false").strip().lower()
    values = {"true": True, "false": False, "1": True, "0": False}
    if raw not in values:
        raise ValueError("Invalid DURABLE_AGENT_RUNTIME_ENABLED value")
    return DurableAgentSettings(enabled=values[raw])


class DurableAgentRuntime:
    """Thin composition; every security and persistence decision stays delegated."""

    def __init__(
        self,
        planner: DurableControlledPlanner,
        worker: DurableWorkerRuntime,
        *,
        settings: DurableAgentSettings | None = None,
    ):
        self.settings = settings or load_durable_agent_settings()
        if not self.settings.enabled:
            raise ValueError("durable_agent_runtime_disabled")
        self._planner = planner
        self._worker = worker

    def submit(
        self,
        request: PlanningRequest,
        *,
        scope_key: str,
        idempotency_key: str,
        now: datetime,
    ) -> ExecutionCreation:
        return self._planner.submit(
            request,
            scope_key=scope_key,
            idempotency_key=idempotency_key,
            now=now,
        )

    def run_next(
        self,
        execution_id: UUID,
        *,
        worker_id: str,
        crash_at: WorkerCrashPoint | None = None,
    ) -> WorkerRunResult:
        return self._worker.run_next(
            execution_id,
            worker_id=worker_id,
            operation=lambda step: self._planner.execute_step(execution_id, step),
            crash_at=crash_at,
        )

    def run_to_terminal(
        self,
        execution_id: UUID,
        *,
        worker_id_prefix: str,
    ) -> DurableAgentResult:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,110}", worker_id_prefix):
            raise ValueError("invalid worker ID prefix")
        outcomes = []
        for index in range(self.settings.max_steps_per_run):
            outcome = self.run_next(
                execution_id,
                worker_id=f"{worker_id_prefix}-{index}",
            )
            outcomes.append(outcome)
            if outcome.outcome == "step_completed":
                continue
            if outcome.outcome == "execution_completed":
                output = self._planner.final_output(execution_id)
                if output is None:
                    raise RuntimeError("completed execution has no final output")
                return DurableAgentResult(
                    execution_id=execution_id,
                    status="completed",
                    worker_outcomes=tuple(outcomes),
                    output=output,
                )
            if outcome.outcome == "terminal":
                output = self._planner.final_output(execution_id)
                return DurableAgentResult(
                    execution_id=execution_id,
                    status="completed" if output is not None else "failed",
                    worker_outcomes=tuple(outcomes),
                    output=output,
                )
            status = {
                "reconciliation_required": "reconciling",
                "lease_lost": "paused",
                "lease_unavailable": "paused",
                "crashed": "paused",
            }[outcome.outcome]
            return DurableAgentResult(
                execution_id=execution_id,
                status=status,
                worker_outcomes=tuple(outcomes),
                output=None,
            )
        return DurableAgentResult(
            execution_id=execution_id,
            status="incomplete",
            worker_outcomes=tuple(outcomes),
            output=None,
        )
