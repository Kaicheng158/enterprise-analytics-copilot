"""Build messages from the server-selected immutable prompt release."""
from backend.prompt_registry import active_release
# Compatibility exports for historical component measurements; not request configuration.
from backend.prompt_analytics_v1 import SYSTEM_PROMPT_V1, OUTPUT_CONTRACT


def build_messages(user_message: str) -> list[dict[str, str]]:
    return active_release().messages(user_message)
