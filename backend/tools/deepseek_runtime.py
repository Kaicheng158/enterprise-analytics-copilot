"""Disabled-by-default composition boundary for DeepSeek tool orchestration."""

from .catalog import build_tool_orchestrator
from .deepseek_adapter import (
    DeepSeekToolDecisionProvider,
    DeepSeekToolSettings,
    load_deepseek_tool_settings,
)
from .sales_db import ConnectionFactory, open_sales_connection


def build_deepseek_tool_runtime(
    connection_factory: ConnectionFactory = open_sales_connection,
    settings: DeepSeekToolSettings | None = None,
):
    effective = settings or load_deepseek_tool_settings()
    provider = DeepSeekToolDecisionProvider(effective)
    return build_tool_orchestrator(provider, connection_factory), provider
