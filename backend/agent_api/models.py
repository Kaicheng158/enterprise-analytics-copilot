"""Strict public-safe contracts for the default-off internal Agent API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from backend.planning.models import (
    Period,
    Region,
    SalesGrowthComparison,
    TaskName,
)
from backend.tools.models import StrictModel


class AgentTaskRequest(StrictModel):
    task: TaskName
    baseline_period: Period
    comparison_period: Period
    region: Region

    @model_validator(mode="after")
    def periods_must_differ(self):
        if self.baseline_period == self.comparison_period:
            raise ValueError("comparison periods must differ")
        return self


class AgentCaller(StrictModel):
    """Server-created identity; never populated from request fields."""

    principal: Literal["analytics-copilot-internal"] = "analytics-copilot-internal"
    scope_key: Literal["internal-agent:analytics-copilot"] = (
        "internal-agent:analytics-copilot"
    )


class AgentTaskProgress(StrictModel):
    completed_steps: int = Field(ge=0)
    total_steps: int = Field(ge=1, le=8)

    @model_validator(mode="after")
    def completed_cannot_exceed_total(self):
        if self.completed_steps > self.total_steps:
            raise ValueError("completed steps exceed total steps")
        return self


class AgentTaskError(StrictModel):
    code: Literal[
        "agent_execution_failed",
        "agent_reconciliation_required",
    ]
    message: str = Field(min_length=1, max_length=160)


class AgentTaskResponse(StrictModel):
    response_version: Literal["internal-agent-task-v1"] = "internal-agent-task-v1"
    task_id: UUID
    status: Literal[
        "queued",
        "running",
        "needs_reconciliation",
        "completed",
        "failed",
    ]
    created: bool | None = None
    request: AgentTaskRequest
    progress: AgentTaskProgress
    result: SalesGrowthComparison | None = None
    error: AgentTaskError | None = None
    state_version: int = Field(ge=0)
    policy_version: str = Field(min_length=1, max_length=80)
    runtime_version: str = Field(min_length=1, max_length=80)
    updated_at: datetime

    @model_validator(mode="after")
    def terminal_fields_match_status(self):
        if self.updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")
        if self.status == "completed":
            if self.result is None or self.error is not None:
                raise ValueError("completed task requires result and no error")
        elif self.result is not None:
            raise ValueError("only completed task may contain a result")
        if self.status in {"failed", "needs_reconciliation"}:
            if self.error is None:
                raise ValueError("stopped task requires a safe error")
        elif self.error is not None:
            raise ValueError("active/successful task cannot contain an error")
        return self
