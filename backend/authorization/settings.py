"""Strict default-off environment loading for product authorization."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from .models import ProjectAuthorizationSettings


def load_project_authorization_settings() -> ProjectAuthorizationSettings:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    raw = os.getenv("PROJECT_AUTHORIZATION_ENABLED", "false").strip().lower()
    values = {"true": True, "false": False, "1": True, "0": False}
    if raw not in values:
        raise ValueError("Invalid PROJECT_AUTHORIZATION_ENABLED value")
    return ProjectAuthorizationSettings(enabled=values[raw])
