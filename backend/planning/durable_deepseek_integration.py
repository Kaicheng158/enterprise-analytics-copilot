"""Default-off composition of structured Planner transport and durable execution."""

from pydantic import Field

from backend.tools.catalog import build_tool_registry
from backend.tools.executor import ToolExecutor
from backend.tools.models import StrictModel
from backend.tools.sales_db import ConnectionFactory, open_sales_connection

from .checkpoint_store import PostgresCheckpointStore
from .durable_agent_runtime import (
    DurableAgentRuntime,
    DurableAgentSettings,
    load_durable_agent_settings,
)
from .durable_controlled_runtime import (
    DurableControlledPlanner,
    DurableControlledPlannerSettings,
    load_durable_controlled_planner_settings,
)
from .structured_provider import PlannerTransport, StructuredPlanProvider
from .worker_runtime import (
    DurableWorkerRuntime,
    DurableWorkerSettings,
    load_durable_worker_settings,
)


class DurableDeepSeekIntegrationSettings(StrictModel):
    """Reuse the existing independent server-owned feature boundaries."""

    planner: DurableControlledPlannerSettings = Field(
        default_factory=DurableControlledPlannerSettings
    )
    worker: DurableWorkerSettings = Field(default_factory=DurableWorkerSettings)
    agent: DurableAgentSettings = Field(default_factory=DurableAgentSettings)


def load_durable_deepseek_integration_settings() -> DurableDeepSeekIntegrationSettings:
    return DurableDeepSeekIntegrationSettings(
        planner=load_durable_controlled_planner_settings(),
        worker=load_durable_worker_settings(),
        agent=load_durable_agent_settings(),
    )


def build_durable_deepseek_agent_runtime(
    transport: PlannerTransport,
    store: PostgresCheckpointStore,
    *,
    settings: DurableDeepSeekIntegrationSettings | None = None,
    connection_factory: ConnectionFactory = open_sales_connection,
) -> tuple[DurableAgentRuntime, StructuredPlanProvider]:
    """Compose existing boundaries without adding an execution or auth path."""

    configured = settings or load_durable_deepseek_integration_settings()
    if not (
        configured.planner.enabled
        and configured.worker.enabled
        and configured.agent.enabled
    ):
        raise ValueError("durable_deepseek_integration_disabled")

    provider = StructuredPlanProvider(transport)
    registry = build_tool_registry(connection_factory)
    planner = DurableControlledPlanner(
        provider,
        store,
        ToolExecutor(registry),
        settings=configured.planner,
    )
    worker = DurableWorkerRuntime(store, settings=configured.worker)
    return (
        DurableAgentRuntime(planner, worker, settings=configured.agent),
        provider,
    )
