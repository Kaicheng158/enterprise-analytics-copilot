"""Strict contracts for server-owned project authorization."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from backend.tools.models import StrictModel


ProjectAction = Literal[
    "project_read",
    "project_write",
    "asset_read",
    "asset_write",
    "task_submit",
    "task_read",
    "task_execute",
    "project_revoke",
]


class ProjectAuthorizationSettings(StrictModel):
    enabled: bool = False
    policy_version: Literal["project-authorization-v1"] = "project-authorization-v1"


class ProjectOwner(StrictModel):
    project_id: UUID
    owner_user_id: UUID
    state: Literal["active", "revoked"]
    authorization_version: int = Field(gt=0)
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None = None


class ProjectTaskBinding(StrictModel):
    execution_id: UUID
    project_id: UUID
    owner_user_id: UUID
    authorization_version: int = Field(gt=0)
    state: Literal["active", "revoked"]
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None = None


class AuthorizedResource(StrictModel):
    policy_version: Literal["project-authorization-v1"] = "project-authorization-v1"
    user_id: UUID
    project_id: UUID
    action: ProjectAction
    authorization_version: int = Field(gt=0)
    asset_id: UUID | None = None
    execution_id: UUID | None = None


class AuthorizationFailure(Exception):
    """Content-free failure code safe for service boundaries."""

    def __init__(self, code: str = "authorization_denied"):
        self.code = code
        super().__init__(code)
