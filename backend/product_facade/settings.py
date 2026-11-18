"""Default-off settings loader for the localhost Product Facade."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import SecretStr

from .models import ProductDatasetSettings, ProductFacadeSettings


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


def validate_product_dataset_settings(settings: ProductDatasetSettings) -> ProductDatasetSettings:
    if not settings.enabled:
        return settings
    if len(settings.idempotency_secret.get_secret_value()) < 32:
        raise ValueError("PRODUCT_DATASET_IDEMPOTENCY_SECRET must contain at least 32 characters")
    if not settings.storage_root:
        raise ValueError("PRODUCT_DATASET_STORAGE_ROOT is required when upload is enabled")
    root = Path(settings.storage_root).expanduser()
    if not root.is_absolute():
        raise ValueError("PRODUCT_DATASET_STORAGE_ROOT must be absolute")
    return settings.model_copy(update={"storage_root": str(root)})


def load_product_dataset_settings() -> ProductDatasetSettings:
    return validate_product_dataset_settings(
        ProductDatasetSettings(
            enabled=_boolean("PRODUCT_DATASET_UPLOAD_ENABLED"),
            idempotency_secret=SecretStr(os.getenv("PRODUCT_DATASET_IDEMPOTENCY_SECRET", "")),
            storage_root=os.getenv("PRODUCT_DATASET_STORAGE_ROOT", ""),
            max_file_bytes=int(
                os.getenv("PRODUCT_DATASET_MAX_FILE_BYTES", str(2 * 1024 * 1024))
            ),
            max_schema_header_bytes=int(
                os.getenv("PRODUCT_DATASET_MAX_SCHEMA_HEADER_BYTES", "8192")
            ),
        )
    )
