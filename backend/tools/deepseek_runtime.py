"""Disabled-by-default composition boundary for DeepSeek tool orchestration."""

from .deepseek_adapter import (
    DeepSeekToolDecisionProvider,
    DeepSeekToolSettings,
    load_deepseek_tool_settings,
)
from .exposed_catalog import ToolExposureProfile, build_exposed_tool_orchestrator
from .sales_db import ConnectionFactory, open_sales_connection


def build_deepseek_tool_runtime(
    connection_factory: ConnectionFactory = open_sales_connection,
    settings: DeepSeekToolSettings | None = None,
    exposure_profile: ToolExposureProfile = "production",
):
    effective = settings or load_deepseek_tool_settings()
    provider = DeepSeekToolDecisionProvider(effective)
    return (
        build_exposed_tool_orchestrator(
            provider,
            connection_factory,
            exposure_profile=exposure_profile,
        ),
        provider,
    )
