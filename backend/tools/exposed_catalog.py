"""Versioned composition root for model-facing tool exposure."""

from typing import Literal

from .catalog import (
    INTERNAL_PRINCIPAL,
    build_tool_registry,
)
from .executor import ToolExecutor
from .exposure import (
    ExposureEnforcingProvider,
    ToolExposurePolicy,
    ToolExposureRegistry,
)
from .orchestration import ToolDecisionProvider, ToolOrchestrator
from .sales_db import ConnectionFactory, open_sales_connection

ToolExposureProfile = Literal["production", "evaluation_in_memory"]

PRODUCTION_TOOL_NAMES = frozenset({"get_postgres_sales_summary"})
EVALUATION_IN_MEMORY_TOOL_NAMES = frozenset({"get_sales_summary"})


def _tool_names(profile: ToolExposureProfile) -> frozenset[str]:
    if profile == "production":
        return PRODUCTION_TOOL_NAMES
    if profile == "evaluation_in_memory":
        return EVALUATION_IN_MEMORY_TOOL_NAMES
    raise ValueError("Unknown server-owned tool exposure profile")


def build_exposed_tool_registry(
    connection_factory: ConnectionFactory = open_sales_connection,
    exposure_profile: ToolExposureProfile = "production",
) -> ToolExposureRegistry:
    registry = build_tool_registry(connection_factory)
    policy = ToolExposurePolicy(
        grants={INTERNAL_PRINCIPAL: _tool_names(exposure_profile)}
    )
    return ToolExposureRegistry(registry, policy)


def build_exposed_tool_orchestrator(
    provider: ToolDecisionProvider,
    connection_factory: ConnectionFactory = open_sales_connection,
    exposure_profile: ToolExposureProfile = "production",
) -> ToolOrchestrator:
    registry = build_exposed_tool_registry(connection_factory, exposure_profile)
    guarded_provider = ExposureEnforcingProvider(provider)
    return ToolOrchestrator(registry, ToolExecutor(registry), guarded_provider)
