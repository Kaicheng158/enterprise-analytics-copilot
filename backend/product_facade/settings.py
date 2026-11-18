"""Default-off settings loader for the localhost Product Facade."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from .models import ProductFacadeSettings


def _boolean(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "true" if default else "false").strip().lower()
    values = {"true": True, "false": False, "1": True, "0": False}
    if raw not in values:
        raise ValueError(f"Invalid {name} value")
    return values[raw]


def validate_product_facade_settings(settings: ProductFacadeSettings) -> ProductFacadeSettings:
    parsed = urlsplit(settings.allowed_origin)
    try:
        parsed.port
    except ValueError as error:
        raise ValueError("PRODUCT_FACADE_ALLOWED_ORIGIN has an invalid port") from error
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Product Facade requires an HTTP loopback origin")
    return settings


def load_product_facade_settings() -> ProductFacadeSettings:
    return validate_product_facade_settings(
        ProductFacadeSettings(
            enabled=_boolean("PRODUCT_FACADE_ENABLED"),
            allowed_origin=os.getenv("PRODUCT_FACADE_ALLOWED_ORIGIN", "http://127.0.0.1:8775"),
            default_project_quota_bytes=int(
                os.getenv("PRODUCT_FACADE_DEFAULT_PROJECT_QUOTA_BYTES", str(10 * 1024 * 1024))
            ),
            lifecycle_policy_version=os.getenv(
                "PRODUCT_FACADE_LIFECYCLE_POLICY_VERSION", "local-private-v1"
            ),
        )
    )
