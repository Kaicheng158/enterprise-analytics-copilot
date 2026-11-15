"""Strict metadata-only contracts for PV2-02a private persistence."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PersistenceContract(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, hide_input_in_errors=True)


class DatasetPersistenceSettings(PersistenceContract):
    enabled: bool = False
    coordination_version: Literal["dataset-storage-v1"] = "dataset-storage-v1"


class Project(PersistenceContract):
    project_id: UUID
    scope_key: str = Field(min_length=1, max_length=160)
    state: Literal["active", "archived", "deleting", "deleted"]
    quota_bytes: int = Field(gt=0)
    used_bytes: int = Field(ge=0)
    reserved_bytes: int = Field(ge=0)
    lifecycle_policy_version: str = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def quota_is_coherent(self):
        if self.used_bytes + self.reserved_bytes > self.quota_bytes:
            raise ValueError("incoherent quota")
        return self


class Asset(PersistenceContract):
    project_id: UUID
    asset_id: UUID
    asset_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    state: Literal["active", "expired", "deleting", "deleted"]


class AssetRevision(PersistenceContract):
    project_id: UUID
    asset_id: UUID
    revision_id: UUID
    revision_number: int = Field(gt=0)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalized_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parse_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_format: Literal["csv", "xlsx"]
    source_bytes: int = Field(ge=0)
    state: Literal["pending", "ready", "reconciling", "failed", "expired", "deleting", "deleted"]
    parser_policy_version: str
    lifecycle_policy_version: str
    retention_until: datetime | None = None


class DatasetRevision(PersistenceContract):
    project_id: UUID
    dataset_revision_id: UUID
    asset_revision_id: UUID
    normalized_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_contract_version: str
    parser_policy_version: str
    dataset_bytes: int = Field(ge=0)
    state: Literal["pending", "ready", "reconciling", "failed", "expired", "deleting", "deleted"]


class PersistedDataset(PersistenceContract):
    project: Project
    asset: Asset
    asset_revision: AssetRevision
    dataset_revision: DatasetRevision
    reused: bool
    operation_id: UUID


class ReconciliationResult(PersistenceContract):
    operation_id: UUID
    state: Literal["ready", "failed"]
    action: Literal["published_verified_blobs", "failed_missing_or_mismatched_blob"]


class PersistenceFailure(Exception):
    """Fixed content-free code suitable for a trusted service boundary."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
