"""Strict, content-free contracts for PV2-02b lifecycle maintenance."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LifecycleContract(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, hide_input_in_errors=True)


class DatasetLifecycleSettings(LifecycleContract):
    enabled: bool = False
    policy_version: Literal["dataset-lifecycle-v1"] = "dataset-lifecycle-v1"
    max_items_per_run: int = Field(default=100, ge=1, le=1000)
    lease_seconds: int = Field(default=30, ge=1, le=300)


class LifecycleOperationResult(LifecycleContract):
    operation_id: UUID
    project_id: UUID
    revision_id: UUID
    state: Literal["marked", "removing", "reconciling", "completed"]
    action: Literal["delete_revision"] = "delete_revision"
    logical_bytes: int = Field(ge=0)
    quota_released_bytes: int = Field(ge=0)


class MaintenanceResult(LifecycleContract):
    run_id: UUID
    policy_version: Literal["dataset-lifecycle-v1"]
    requested_limit: int = Field(gt=0)
    processed_items: int = Field(ge=0)
    expired_revisions: int = Field(ge=0)
    storage_ready: int = Field(ge=0)
    storage_failed: int = Field(ge=0)
    deletions_completed: int = Field(ge=0)
    deletions_deferred: int = Field(ge=0)
    orphan_blobs_removed: int = Field(ge=0)
    staging_files_removed: int = Field(ge=0)
    cleanup_file_bytes_removed: int = Field(ge=0)
    physical_bytes_reclaimed: int = Field(ge=0)
    truncated: bool
    completed_at: datetime
