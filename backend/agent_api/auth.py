"""Server-owned feature and authorization boundary for the internal Agent API."""

from __future__ import annotations

import os
from pathlib import Path
from secrets import compare_digest
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field, SecretStr, model_validator

from backend.tools.models import StrictModel

from .models import AgentCaller


class InternalAgentApiSettings(StrictModel):
    enabled: bool = False
    api_key: SecretStr = SecretStr("")
    principal: Literal["analytics-copilot-internal"] = Field(
        default="analytics-copilot-internal", frozen=True
    )
    scope_key: Literal["internal-agent:analytics-copilot"] = Field(
        default="internal-agent:analytics-copilot", frozen=True
    )

    @model_validator(mode="after")
    def enabled_api_requires_strong_key(self):
        if self.enabled and len(self.api_key.get_secret_value()) < 32:
            raise ValueError("enabled internal Agent API requires a key of at least 32 characters")
        return self


def load_internal_agent_api_settings() -> InternalAgentApiSettings:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    raw = os.getenv("INTERNAL_AGENT_API_ENABLED", "false").strip().lower()
    values = {"true": True, "false": False, "1": True, "0": False}
    if raw not in values:
        raise ValueError("Invalid INTERNAL_AGENT_API_ENABLED value")
    return InternalAgentApiSettings(
        enabled=values[raw],
        api_key=os.getenv("INTERNAL_AGENT_API_KEY", ""),
    )


class AgentAuthorizationError(RuntimeError):
    def __init__(self, *, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class InternalAgentAuthorizer:
    def __init__(self, settings: InternalAgentApiSettings):
        self.settings = settings

    def authorize(self, presented_key: str | None) -> AgentCaller:
        if not self.settings.enabled:
            raise AgentAuthorizationError(
                status_code=503,
                code="agent_api_disabled",
                message="Internal Agent API is not enabled",
            )
        expected = self.settings.api_key.get_secret_value()
        if not presented_key or not compare_digest(presented_key, expected):
            raise AgentAuthorizationError(
                status_code=401,
                code="agent_unauthorized",
                message="Internal Agent authorization failed",
            )
        return AgentCaller(
            principal=self.settings.principal,
            scope_key=self.settings.scope_key,
        )
