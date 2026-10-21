"""Strict contracts for durable planning checkpoints and reconciliation."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.tools.models import StrictModel

from .models import AgentState, Plan


ExecutionStatus = Literal[
    "pending", "running", "reconciling", "completed", "failed"
]
ReceiptStatus = Literal["started", "succeeded", "failed", "ambiguous"]
HEX_SHA256 = r"^[0-9a-f]{64}$"


class PersistentExecution(StrictModel):
    execution_id: UUID
    scope_key: str = Field(min_length=1, max_length=160)
    idempotency_key_hash: str = Field(pattern=HEX_SHA256)
    request_sha256: str = Field(pattern=HEX_SHA256)
    plan_sha256: str = Field(pattern=HEX_SHA256)
    plan_version: str = Field(min_length=1, max_length=80)
    plan: Plan
    policy_version: str = Field(min_length=1, max_length=80)
    runtime_version: str = Field(min_length=1, max_length=80)
    status: ExecutionStatus
    state_version: int = Field(ge=0)
    checkpoint_sequence: int = Field(ge=0)
    lease_owner: str | None = Field(default=None, max_length=128)
    lease_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", "lease_expires_at")
    @classmethod
    def timestamps_are_timezone_aware(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("checkpoint timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def durable_fields_are_consistent(self):
        if (self.lease_owner is None) != (self.lease_expires_at is None):
            raise ValueError("lease owner and expiry must be set together")
        if self.state_version != self.checkpoint_sequence:
            raise ValueError("execution state/checkpoint versions must match")
        if self.updated_at < self.created_at:
            raise ValueError("execution update cannot precede creation")
        return self


class PersistentCheckpoint(StrictModel):
    execution_id: UUID
    sequence: int = Field(ge=0)
    state_version: int = Field(ge=0)
    execution_status: ExecutionStatus
    agent_state: AgentState
    state_sha256: str = Field(pattern=HEX_SHA256)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def timestamp_is_timezone_aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("checkpoint timestamp must be timezone-aware")
        return value

    @model_validator(mode="after")
    def sequence_matches_state_version(self):
        if self.sequence != self.state_version:
            raise ValueError("checkpoint sequence/state version must match")
        return self


class PersistentStepReceipt(StrictModel):
    execution_id: UUID
    step_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    action_name: str = Field(min_length=1, max_length=100)
    idempotency_token: str = Field(pattern=HEX_SHA256)
    input_sha256: str = Field(pattern=HEX_SHA256)
    status: ReceiptStatus
    output: dict[str, Any] | None = None
    output_sha256: str | None = Field(default=None, pattern=HEX_SHA256)
    tool_call_id: str | None = Field(default=None, max_length=160)
    error_code: str | None = Field(default=None, max_length=100)
    started_at: datetime
    finished_at: datetime | None = None

    @field_validator("started_at", "finished_at")
    @classmethod
    def timestamps_are_timezone_aware(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("receipt timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def fields_match_status(self):
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("receipt finish cannot precede start")
        if self.status == "started" and (
            self.output is not None
            or self.output_sha256 is not None
            or self.finished_at is not None
        ):
            raise ValueError("started receipt cannot contain a result")
        if self.status == "succeeded" and (
            self.output is None
            or self.output_sha256 is None
            or self.finished_at is None
            or self.error_code is not None
        ):
            raise ValueError("successful receipt requires a result")
        if self.status in {"failed", "ambiguous"} and (
            self.output is not None
            or self.output_sha256 is not None
            or self.finished_at is None
            or self.error_code is None
        ):
            raise ValueError("failed/ambiguous receipt requires a terminal error")
        return self


class ExecutionCreation(StrictModel):
    execution: PersistentExecution
    checkpoint: PersistentCheckpoint
    created: bool


class StepStart(StrictModel):
    execution: PersistentExecution
    checkpoint: PersistentCheckpoint | None
    receipt: PersistentStepReceipt
    deduplicated: bool


class RecoveryDecision(StrictModel):
    outcome: Literal[
        "resumable", "terminal", "reconciliation_required", "lease_unavailable"
    ]
    execution_id: UUID
    state_version: int = Field(ge=0)
    next_step_id: str | None = None
    reason: Literal[
        "dependency_ready",
        "execution_terminal",
        "ambiguous_step",
        "inconsistent_checkpoint",
        "active_lease",
    ]

    @model_validator(mode="after")
    def next_step_matches_outcome(self):
        if (self.outcome == "resumable") != (self.next_step_id is not None):
            raise ValueError("only resumable decisions identify a next step")
        return self


class SimulationResult(StrictModel):
    outcome: Literal[
        "step_completed",
        "execution_completed",
        "crashed",
        "reconciliation_required",
        "terminal",
        "lease_unavailable",
    ]
    execution_id: UUID
    step_id: str | None = None
    operation_calls: int = Field(ge=0)
    state_version: int = Field(ge=0)


class ExecutionConsistency(StrictModel):
    execution_id: UUID
    state_version: int = Field(ge=0)
    checkpoint_sequence: int = Field(ge=0)
    receipt_count: int = Field(ge=0)
    completed_receipt_count: int = Field(ge=0)
    status: Literal["consistent"] = "consistent"


class ReconciliationCase(StrictModel):
    execution_id: UUID
    step_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    action_name: str = Field(min_length=1, max_length=100)
    idempotency_token: str = Field(pattern=HEX_SHA256)
    reason: Literal["outcome_unknown"] = "outcome_unknown"
    retry_allowed: Literal[False] = False
    required_action: Literal["verify_external_outcome"] = "verify_external_outcome"
