"""Default-off composition boundary for bounded planning."""

from __future__ import annotations

import os

from backend.tools.executor import ToolExecutor
from backend.tools.exposed_catalog import build_exposed_tool_registry
from backend.tools.models import StrictModel
from backend.tools.sales_db import ConnectionFactory, open_sales_connection

from .executor import BoundedPlanExecutor
from .models import PlanningLimits
from .orchestration import BoundedPlanningOrchestrator, PlanProvider


class PlanningSettings(StrictModel):
    enabled: bool = False
    limits: PlanningLimits = PlanningLimits()


def load_planning_settings() -> PlanningSettings:
    raw = os.getenv("MULTI_STEP_PLANNING_ENABLED", "false").strip().lower()
    if raw not in {"true", "false", "1", "0"}:
        raise ValueError("Invalid MULTI_STEP_PLANNING_ENABLED value")
    return PlanningSettings(enabled=raw in {"true", "1"})


def build_planning_runtime(
    provider: PlanProvider,
    *,
    settings: PlanningSettings | None = None,
    connection_factory: ConnectionFactory = open_sales_connection,
) -> BoundedPlanningOrchestrator:
    effective = settings or load_planning_settings()
    if not effective.enabled:
        raise ValueError("multi_step_planning_disabled")
    registry = build_exposed_tool_registry(
        connection_factory=connection_factory,
        exposure_profile="production",
    )
    executor = BoundedPlanExecutor(
        ToolExecutor(registry), limits=effective.limits
    )
    return BoundedPlanningOrchestrator(provider, executor)
