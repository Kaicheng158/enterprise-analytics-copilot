"""Default-off composition for a future controlled LLM planner transport."""

from __future__ import annotations

import os

from backend.tools.models import StrictModel
from backend.tools.sales_db import ConnectionFactory, open_sales_connection

from .runtime import PlanningSettings, build_planning_runtime, load_planning_settings
from .structured_provider import (
    PlannerGenerationConfig,
    PlannerTransport,
    StructuredPlanProvider,
)


class ControlledPlannerSettings(StrictModel):
    enabled: bool = False
    generation: PlannerGenerationConfig = PlannerGenerationConfig()


def _parse_planner_feature_flag(raw: str) -> bool:
    values = {"true": True, "false": False, "1": True, "0": False}
    try:
        return values[raw.strip().lower()]
    except KeyError:
        raise ValueError("Invalid LLM_PLANNER_ENABLED value") from None


def load_controlled_planner_settings() -> ControlledPlannerSettings:
    return ControlledPlannerSettings(
        enabled=_parse_planner_feature_flag(
            os.getenv("LLM_PLANNER_ENABLED", "false")
        )
    )


def build_controlled_planner_runtime(
    transport: PlannerTransport,
    *,
    planner_settings: ControlledPlannerSettings | None = None,
    planning_settings: PlanningSettings | None = None,
    connection_factory: ConnectionFactory = open_sales_connection,
):
    controlled = planner_settings or load_controlled_planner_settings()
    if not controlled.enabled:
        raise ValueError("llm_planner_disabled")
    bounded = planning_settings or load_planning_settings()
    if not bounded.enabled:
        raise ValueError("multi_step_planning_disabled")
    provider = StructuredPlanProvider(
        transport,
        config=controlled.generation,
    )
    orchestrator = build_planning_runtime(
        provider,
        settings=bounded,
        connection_factory=connection_factory,
    )
    return orchestrator, provider
