"""Default-off settings and cookie policy for the local authentication surface."""

from __future__ import annotations

import os
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator

from backend.tools.models import StrictModel


def _boolean(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "true" if default else "false").strip().lower()
    values = {"true": True, "false": False, "1": True, "0": False}
    if raw not in values:
        raise ValueError(f"Invalid {name} value")
    return values[raw]


class LocalAuthSettings(StrictModel):
    enabled: bool = False
    deployment_mode: Literal["local", "production"] = "local"
    allowed_origin: str = "http://127.0.0.1:8775"
    cookie_name: str = Field(default="eac_local_session", pattern=r"^[a-z][a-z0-9_]{2,63}$")
    cookie_secure: bool = False
    cookie_same_site: Literal["strict"] = "strict"
    session_ttl_seconds: int = Field(default=3600, ge=300, le=86_400)
    failed_login_limit: int = Field(default=5, ge=2, le=20)
    failed_login_window_seconds: int = Field(default=300, ge=30, le=3600)
    login_block_seconds: int = Field(default=300, ge=30, le=3600)
    rate_limit_secret: SecretStr = Field(default=SecretStr(""))

    @model_validator(mode="after")
    def secure_deployment_contract(self):
        parsed = urlsplit(self.allowed_origin)
        try:
            parsed.port
        except ValueError as error:
            raise ValueError("allowed_origin contains an invalid port") from error
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("allowed_origin must be an origin only")
        if parsed.path not in {"", "/"} or not parsed.hostname:
            raise ValueError("allowed_origin must be an origin only")
        loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if self.deployment_mode == "local":
            if parsed.scheme != "http" or not loopback:
                raise ValueError("local mode requires an HTTP loopback origin")
        elif parsed.scheme != "https" or not self.cookie_secure:
            raise ValueError("production mode requires HTTPS and Secure cookies")
        if self.enabled and len(self.rate_limit_secret.get_secret_value()) < 32:
            raise ValueError("enabled auth requires a server-owned rate-limit secret")
        return self


def load_local_auth_settings() -> LocalAuthSettings:
    return LocalAuthSettings(
        enabled=_boolean("LOCAL_AUTH_ENABLED"),
        deployment_mode=os.getenv("LOCAL_AUTH_DEPLOYMENT_MODE", "local"),
        allowed_origin=os.getenv("LOCAL_AUTH_ALLOWED_ORIGIN", "http://127.0.0.1:8775"),
        cookie_name=os.getenv("LOCAL_AUTH_COOKIE_NAME", "eac_local_session"),
        cookie_secure=_boolean("LOCAL_AUTH_COOKIE_SECURE"),
        session_ttl_seconds=int(os.getenv("LOCAL_AUTH_SESSION_TTL_SECONDS", "3600")),
        failed_login_limit=int(os.getenv("LOCAL_AUTH_FAILED_LOGIN_LIMIT", "5")),
        failed_login_window_seconds=int(os.getenv("LOCAL_AUTH_FAILED_LOGIN_WINDOW_SECONDS", "300")),
        login_block_seconds=int(os.getenv("LOCAL_AUTH_LOGIN_BLOCK_SECONDS", "300")),
        rate_limit_secret=SecretStr(os.getenv("LOCAL_AUTH_RATE_LIMIT_SECRET", "")),
    )
