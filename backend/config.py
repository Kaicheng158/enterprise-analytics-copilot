"""Single configuration boundary: runtime settings and versioned provider catalog."""
import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr, field_validator

DEFAULT_PROVIDER = "deepseek"
DEFAULT_SYSTEM_MESSAGE = "You are a helpful assistant."
MAX_USER_CHARACTERS = 8000
MAX_SYSTEM_CHARACTERS = 2000
PROVIDERS = {
    "deepseek": {
        "default_model": "deepseek-flash",
        "models": ("deepseek-flash",),
        "chat_url": "https://api.deepseek.com/chat/completions",
        "api_key_env": "DEEPSEEK_API_KEY",
    }
}
RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})
BACKOFF_CAP_SECONDS = 8.0
BACKOFF_JITTER_FRACTION = 0.25
COST_CURRENCY = "USD"

PRICING_VERSION = "deepseek-2026-09-13"
PRICING_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing/"
PRICES = {
    ("deepseek", "deepseek-flash"): {
        "peak": {"hit": "0.006", "miss": "0.3", "output": "1.2"},
        "off_peak": {"hit": "0.003", "miss": "0.15", "output": "0.6"},
        "peak_weekdays": [0, 1, 2, 3, 4],
        "peak_utc_hours": [(1, 4), (6, 10)],
    }
}


class RetryConfig(BaseModel):
    timeout_seconds: float = Field(default=30, gt=0, le=120, allow_inf_nan=False)
    max_retries: int = Field(default=2, ge=0, le=3)
    backoff_seconds: float = Field(default=1, gt=0, le=5, allow_inf_nan=False)


class LLMSettings(RetryConfig):
    provider: str = DEFAULT_PROVIDER
    model: str = PROVIDERS[DEFAULT_PROVIDER]["default_model"]
    api_key: SecretStr = SecretStr("")
    max_output_tokens: int = Field(default=512, ge=1, le=8192)
    thinking: Literal["disabled"] = "disabled"
    system_message: str = Field(default=DEFAULT_SYSTEM_MESSAGE, min_length=1, max_length=MAX_SYSTEM_CHARACTERS)

    @field_validator("provider")
    @classmethod
    def supported_provider(cls, value):
        if value not in PROVIDERS:
            raise ValueError("Unsupported provider")
        return value

    @field_validator("model", "system_message")
    @classmethod
    def non_blank(cls, value):
        if not value.strip():
            raise ValueError("Must not be blank")
        return value


def load_settings() -> LLMSettings:
    # Environment (including Compose) takes precedence over the local file.
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    provider = os.getenv("LLM_PROVIDER") or DEFAULT_PROVIDER
    if provider not in PROVIDERS:
        raise ValueError("Unsupported provider")
    catalog = PROVIDERS[provider]
    model = os.getenv("LLM_MODEL") or os.getenv("DEEPSEEK_MODEL") or catalog["default_model"]
    if model not in catalog["models"]:
        raise ValueError("Unsupported model for provider")
    names = {
        "timeout_seconds": "LLM_TIMEOUT_SECONDS",
        "max_retries": "LLM_MAX_RETRIES",
        "backoff_seconds": "LLM_BACKOFF_SECONDS",
        "max_output_tokens": "LLM_MAX_OUTPUT_TOKENS",
        "system_message": "LLM_SYSTEM_MESSAGE",
    }
    overrides = {field: os.environ[name] for field, name in names.items() if os.getenv(name) not in (None, "")}
    return LLMSettings(provider=provider, model=model,
                       api_key=os.getenv(catalog["api_key_env"], ""), **overrides)
