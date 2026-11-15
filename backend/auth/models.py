"""Strict, provider-independent identity and session contracts for PV2-03a."""

from __future__ import annotations

from datetime import datetime
import re
import unicodedata
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator

from backend.tools.models import StrictModel


EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}$")


def normalize_account(value: str) -> str:
    """Normalize the only account format supported in this phase: an email address."""
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    if len(normalized) > 254 or any(ord(character) < 32 for character in normalized) or not EMAIL_RE.fullmatch(normalized):
        raise ValueError("account must be a valid email address")
    local, domain = normalized.rsplit("@", 1)
    if "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise ValueError("account must be a valid email address")
    if any(part == "" for part in domain.split(".")):
        raise ValueError("account must be a valid email address")
    return f"{local}@{domain}"


class RegisterRequest(StrictModel):
    account: str = Field(min_length=3, max_length=254)
    display_name: str = Field(min_length=1, max_length=80)
    password: SecretStr = Field(min_length=12, max_length=128)

    @field_validator("account")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return normalize_account(value)

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        normalized = " ".join(unicodedata.normalize("NFKC", value).strip().split())
        if not normalized or any(ord(character) < 32 for character in normalized):
            raise ValueError("display_name contains unsupported characters")
        return normalized


class LoginRequest(StrictModel):
    account: str = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("account")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return normalize_account(value)


class UserRecord(StrictModel):
    user_id: UUID
    account: str
    display_name: str
    state: Literal["active", "disabled"]
    created_at: datetime


class SessionRecord(StrictModel):
    session_id: UUID
    user_id: UUID
    state: Literal["active", "revoked", "expired"]
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime
    revoked_at: datetime | None = None

    @model_validator(mode="after")
    def timestamps_are_consistent(self):
        if self.expires_at <= self.created_at:
            raise ValueError("session expiry must follow creation")
        if self.state == "revoked" and self.revoked_at is None:
            raise ValueError("revoked session requires revoked_at")
        return self


class AuthPrincipal(StrictModel):
    """Server-created authenticated identity. Client payloads never construct this."""

    principal_version: Literal["local-user-principal-v1"] = "local-user-principal-v1"
    user_id: UUID
    session_id: UUID
    account: str
    display_name: str


class SessionView(StrictModel):
    response_version: Literal["local-auth-session-v1"] = "local-auth-session-v1"
    authenticated: Literal[True] = True
    user: UserRecord
    session_id: UUID
    expires_at: datetime
    csrf_token: str = Field(min_length=32, max_length=256)
    next_path: Literal["/#overview"] = "/#overview"


class SessionStatus(StrictModel):
    response_version: Literal["local-auth-status-v1"] = "local-auth-status-v1"
    authenticated: Literal[True] = True
    user: UserRecord
    session_id: UUID
    expires_at: datetime


class WorkspaceIdentity(StrictModel):
    response_version: Literal["local-workspace-identity-v1"] = "local-workspace-identity-v1"
    destination: Literal["my-workspace"] = "my-workspace"
    principal: AuthPrincipal
    projects_available: Literal[False] = False


class AuthErrorDetail(StrictModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    message: str = Field(min_length=1, max_length=160)


class AuthServiceError(Exception):
    """Safe internal error code; never carries secrets or raw database details."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
