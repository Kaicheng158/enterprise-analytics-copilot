"""Default-off durable orchestration for authorized Dataset analysis plans."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from typing import Literal
from uuid import UUID

from pydantic import ValidationError, model_validator

from backend.authorization import AuthorizationFailure, project_scope_key
from backend.datasets.analysis_models import DatasetToolResult
from backend.tools.dataset_analysis import (
    AuthorizedDatasetAnalysisRuntime,
    DATASET_ANALYSIS_PRINCIPAL,
)
from backend.tools.models import StrictModel, ToolExecutionContext

from .checkpoint_models import AtomicProjectTaskBinding, ExecutionCreation
from .checkpoint_recovery import initial_agent_state
from .checkpoint_store import CheckpointStoreError, PostgresCheckpointStore
from .dataset_models import (
    DatasetAgentClarification,
    DatasetAgentFinalResult,
    DatasetAgentRequest,
    DatasetAgentSettings,
    DatasetAgentTaskView,
    DatasetPlan,
    DatasetPlanIssue,
)
from .dataset_validation import DatasetPlanValidator, canonical_dataset_request_sha256
from .worker_runtime import DurableWorkerRuntime, WorkerCrashPoint, WorkerRunResult


class DatasetAgentError(RuntimeError):
    def __init__(self, code: str, *, issues: tuple[DatasetPlanIssue, ...] = ()):
        super().__init__(code)
        self.code = code
        self.issues = issues


class DatasetAgentRunResult(StrictModel):
    execution_id: UUID
    status: Literal["completed", "reconciling", "paused", "failed", "incomplete"]
    worker_outcomes: tuple[WorkerRunResult, ...]
    output: DatasetAgentFinalResult | None = None

    @model_validator(mode="after")
    def output_matches_status(self):
        if (self.status == "completed") != (self.output is not None):
            raise ValueError("only completed Dataset Agent runs return output")
        return self

    @classmethod
    def from_outcomes(cls, execution_id, status, outcomes, output=None):
        return cls(
            execution_id=execution_id,
            status=status,
            worker_outcomes=tuple(outcomes),
            output=output,
        )


def load_dataset_agent_settings() -> DatasetAgentSettings:
    raw = os.getenv("DATASET_AGENT_ENABLED", "false").strip().lower()
    values = {"true": True, "false": False, "1": True, "0": False}
    if raw not in values:
        raise ValueError("Invalid DATASET_AGENT_ENABLED value")
    return DatasetAgentSettings(enabled=values[raw])


class DurableDatasetPlanner:
    """Validate, atomically bind and execute one immutable Dataset Plan."""

    def __init__(
        self,
        provider,
        store: PostgresCheckpointStore,
        analysis_runtime: AuthorizedDatasetAnalysisRuntime,
        tool_executor,
        *,
        settings: DatasetAgentSettings | None = None,
    ):
        self.settings = settings or load_dataset_agent_settings()
        if not self.settings.enabled:
            raise ValueError("dataset_agent_disabled")
        self._provider = provider
        self._store = store
        self._analysis = analysis_runtime
        self._tools = tool_executor
        self._validator = DatasetPlanValidator(settings=self.settings)

    def submit(
        self,
        request: DatasetAgentRequest,
        *,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> ExecutionCreation:
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            raise ValueError("Dataset Agent time must be timezone-aware")
        if request.project_id != self._analysis.project_id:
            raise DatasetAgentError("dataset_agent_not_authorized")
        try:
            context = self._analysis.binding_context(request.binding)
        except Exception as error:
            raise DatasetAgentError("dataset_agent_not_authorized") from None
        request_sha = canonical_dataset_request_sha256(request, context)
        scope = project_scope_key(request.project_id)
        try:
            existing = self._store.find_execution_by_idempotency(
                scope_key=scope,
                idempotency_key=idempotency_key,
                request_sha256=request_sha,
            )
        except CheckpointStoreError as error:
            raise DatasetAgentError(error.code) from None
        if existing is not None:
            self._analysis.require_task_execution(existing.execution.execution_id)
            return existing

        try:
            raw = self._provider.propose(request, context)
            plan = DatasetPlan.model_validate_json(raw, strict=True)
        except (ValidationError, ValueError, TypeError, UnicodeError):
            raise DatasetAgentError("dataset_plan_invalid") from None
        except Exception:
            raise DatasetAgentError("dataset_plan_provider_error") from None
        validation = self._validator.validate(plan, request, context)
        if validation.status != "valid":
            raise DatasetAgentError("dataset_plan_rejected", issues=validation.issues)
        try:
            final_context = self._analysis.binding_context(request.binding)
        except Exception:
            raise DatasetAgentError("dataset_agent_not_authorized") from None
        if final_context != context:
            raise DatasetAgentError(
                "dataset_binding_changed",
                issues=(DatasetPlanIssue(code="dataset_binding_changed"),),
            )
        principal = self._analysis.principal
        try:
            return self._store.create_or_get_execution(
                scope_key=scope,
                idempotency_key=idempotency_key,
                request_sha256=request_sha,
                plan=plan,
                initial_state=initial_agent_state(plan),
                policy_version=self.settings.policy_version,
                runtime_version=self.settings.runtime_version,
                now=now,
                project_binding=AtomicProjectTaskBinding(
                    project_id=request.project_id,
                    user_id=principal.user_id,
                    session_id=principal.session_id,
                    authorization_version=context.authorization_version,
                ),
            )
        except CheckpointStoreError as error:
            raise DatasetAgentError(error.code) from None

    def execute_step(self, execution_id: UUID, step) -> dict:
        try:
            binding = self._analysis.require_task_execution(execution_id)
        except AuthorizationFailure:
            raise DatasetAgentError("dataset_agent_not_authorized") from None
        if binding.project_id != self._analysis.project_id:
            raise DatasetAgentError("dataset_agent_not_authorized")
        if step.kind == "tool":
            result = self._tools.execute(
                step.tool_call,
                ToolExecutionContext(
                    principal=DATASET_ANALYSIS_PRINCIPAL,
                    correlation_id=f"dataset-{execution_id}-{step.step_id}",
                ),
            )
            if result.status != "success" or result.output is None:
                raise DatasetAgentError(
                    result.error.code if result.error else "tool_execution_failed"
                )
            output = result.output
        else:
            output = self._assemble(execution_id, step)
        try:
            self._analysis.require_task_execution(execution_id)
        except AuthorizationFailure:
            raise DatasetAgentError("dataset_agent_not_authorized") from None
        return output

    def _assemble(self, execution_id: UUID, step) -> dict:
        execution = self._store.get_execution(execution_id)
        if not isinstance(execution.plan, DatasetPlan):
            raise DatasetAgentError("dataset_plan_invalid")
        checkpoint = self._store.get_latest_checkpoint(execution_id)
        states = {item.step_id: item for item in checkpoint.agent_state.step_states}
        results = []
        clarifications = []
        sources = []
        for step_id in step.computation.source_step_ids:
            state = states.get(step_id)
            if state is None or state.status != "completed" or state.output is None:
                raise DatasetAgentError("dataset_state_inconsistent")
            result = DatasetToolResult.model_validate_json(
                json.dumps(state.output, ensure_ascii=False), strict=True
            )
            if result.dataset_revision_id != execution.plan.binding.dataset_revision_id:
                raise DatasetAgentError("dataset_state_inconsistent")
            results.append(result)
            for finding in result.findings:
                if finding.severity == "clarification":
                    candidate = DatasetAgentClarification(
                        code=finding.code, fields=finding.fields
                    )
                    if candidate not in clarifications:
                        clarifications.append(candidate)
            for source in result.sources:
                if source not in sources:
                    sources.append(source)
        status = (
            "blocked"
            if any(item.status == "blocked" for item in results)
            else "needs_clarification"
            if clarifications
            else "completed"
        )
        return DatasetAgentFinalResult(
            plan_id=execution.plan.plan_id,
            project_id=execution.plan.project_id,
            binding=execution.plan.binding,
            normalized_sha256=execution.plan.normalized_sha256,
            status=status,
            results=tuple(results),
            clarifications=tuple(clarifications),
            sources=tuple(sources),
        ).model_dump(mode="json")

    def final_output(self, execution_id: UUID) -> DatasetAgentFinalResult | None:
        execution = self._store.get_execution(execution_id)
        if execution.status != "completed" or not isinstance(execution.plan, DatasetPlan):
            return None
        checkpoint = self._store.get_latest_checkpoint(execution_id)
        return DatasetAgentFinalResult.model_validate_json(
            json.dumps(
                checkpoint.agent_state.step_states[-1].output,
                ensure_ascii=False,
            ),
            strict=True,
        )

    def task_view(self, execution_id: UUID, *, created: bool | None = None) -> DatasetAgentTaskView:
        try:
            self._analysis.require_task_read(execution_id)
        except AuthorizationFailure:
            raise DatasetAgentError("dataset_agent_not_authorized") from None
        execution = self._store.get_execution(execution_id)
        if not isinstance(execution.plan, DatasetPlan) or execution.plan.project_id != self._analysis.project_id:
            raise DatasetAgentError("dataset_agent_not_authorized")
        checkpoint = self._store.get_latest_checkpoint(execution_id)
        operations = []
        for step in execution.plan.steps:
            if step.kind != "tool":
                continue
            arguments = dict(step.tool_call.arguments)
            arguments.pop("binding", None)
            operations.append(
                {
                    "operation_id": step.request_operation_id,
                    "tool_name": step.tool_call.name,
                    "arguments": arguments,
                }
            )
        request = DatasetAgentRequest(
            project_id=execution.plan.project_id,
            binding=execution.plan.binding,
            operations=tuple(operations),
        )
        status = {
            "pending": "queued",
            "running": "running",
            "reconciling": "needs_reconciliation",
            "completed": "completed",
            "failed": "failed",
        }[execution.status]
        return DatasetAgentTaskView(
            task_id=execution_id,
            status=status,
            created=created,
            request=request,
            completed_steps=len(checkpoint.agent_state.completed_step_ids),
            total_steps=len(execution.plan.steps),
            state_version=execution.state_version,
            result=self.final_output(execution_id) if status == "completed" else None,
            error_code=(
                "dataset_reconciliation_required"
                if status == "needs_reconciliation"
                else "dataset_execution_failed"
                if status == "failed"
                else None
            ),
        )


class DurableDatasetAgentRuntime:
    def __init__(
        self,
        planner: DurableDatasetPlanner,
        worker: DurableWorkerRuntime,
        *,
        settings: DatasetAgentSettings | None = None,
    ):
        self.settings = settings or load_dataset_agent_settings()
        if not self.settings.enabled:
            raise ValueError("dataset_agent_disabled")
        self._planner = planner
        self._worker = worker

    def submit(self, request, *, idempotency_key: str, now: datetime | None = None):
        return self._planner.submit(request, idempotency_key=idempotency_key, now=now)

    def get(self, execution_id: UUID, *, created: bool | None = None):
        return self._planner.task_view(execution_id, created=created)

    def run_next(self, execution_id: UUID, *, worker_id: str, crash_at: WorkerCrashPoint | None = None):
        return self._worker.run_next(
            execution_id,
            worker_id=worker_id,
            operation=lambda step: self._planner.execute_step(execution_id, step),
            crash_at=crash_at,
        )

    def run_to_terminal(self, execution_id: UUID, *, worker_id_prefix: str):
        outcomes = []
        for index in range(self.settings.max_steps_per_run):
            outcome = self.run_next(
                execution_id, worker_id=f"{worker_id_prefix}-{index}"
            )
            outcomes.append(outcome)
            if outcome.outcome == "step_completed":
                continue
            if outcome.outcome in {"execution_completed", "terminal"}:
                output = self._planner.final_output(execution_id)
                return DatasetAgentRunResult.from_outcomes(
                    execution_id,
                    "completed" if output is not None else "failed",
                    outcomes,
                    output,
                )
            status = {
                "reconciliation_required": "reconciling",
                "lease_lost": "paused",
                "lease_unavailable": "paused",
                "crashed": "paused",
            }[outcome.outcome]
            return DatasetAgentRunResult.from_outcomes(execution_id, status, outcomes)
        return DatasetAgentRunResult.from_outcomes(execution_id, "incomplete", outcomes)


__all__ = [
    "DatasetAgentError",
    "DatasetAgentRunResult",
    "DurableDatasetAgentRuntime",
    "DurableDatasetPlanner",
    "load_dataset_agent_settings",
]
