"""Compatibility view of the immutable analytics-v1 demonstration data."""
from backend.prompt_analytics_v1 import FEW_SHOT_EXAMPLES


def example_messages() -> list[dict[str, str]]:
    return [
        message
        for user, answer in FEW_SHOT_EXAMPLES
        for message in (
            {'role': 'user', 'content': user},
            {'role': 'assistant', 'content': answer},
        )
    ]
