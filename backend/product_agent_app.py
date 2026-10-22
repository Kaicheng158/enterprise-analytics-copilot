"""Default-off local product slice: Agent API + DeepSeek Planner + queue Worker."""

from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal
import os
from pathlib import Path
from typing import Callable
from urllib.request import urlopen

from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator

from backend.agent_api.api import create_internal_agent_app
from backend.agent_api.auth import (
    InternalAgentApiSettings,
    load_internal_agent_api_settings,
)
from backend.agent_api.local_runtime import (
    DisposableManagedAgentRuntime,
    LocalAgentServiceSettings,
)
from backend.agent_api.ui import add_local_agent_console
from backend.planning.deepseek_planner_adapter import (
    DeepSeekPlannerSettings,
    load_deepseek_planner_settings,
)
from backend.planning.durable_deepseek_integration import (
    DurableDeepSeekIntegrationSettings,
    load_durable_deepseek_integration_settings,
)
from backend.planning.product_planner import DeepSeekProductPlanProvider
from backend.planning.task_queue import (
    PersistentAgentQueueSettings,
    load_persistent_agent_queue_settings,
)
from backend.planning.worker_supervisor import (
    WorkerSupervisorSettings,
    load_worker_supervisor_settings,
)


class LocalAgentProductSettings(BaseModel):
    enabled: bool = False
    max_planner_calls: int = Field(default=10, ge=1, le=100)
    planner_budget_usd: Decimal = Field(default=Decimal("0.02"), gt=0, le=Decimal("1"))
    per_call_reserve_usd: Decimal = Field(
        default=Decimal("0.002"), gt=0, le=Decimal("0.10")
    )

    @model_validator(mode="after")
    def reserve_fits_budget(self):
        if self.per_call_reserve_usd > self.planner_budget_usd:
            raise ValueError("Planner call reserve exceeds product budget")
        return self


def load_local_agent_product_settings() -> LocalAgentProductSettings:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    raw = os.getenv("LOCAL_AGENT_PRODUCT_ENABLED", "false").strip().lower()
    values = {"true": True, "false": False, "1": True, "0": False}
    if raw not in values:
        raise ValueError("Invalid LOCAL_AGENT_PRODUCT_ENABLED value")
    return LocalAgentProductSettings(
        enabled=values[raw],
        max_planner_calls=os.getenv("LOCAL_AGENT_PLANNER_MAX_CALLS", "10"),
        planner_budget_usd=os.getenv("LOCAL_AGENT_PLANNER_BUDGET_USD", "0.02"),
        per_call_reserve_usd=os.getenv(
            "LOCAL_AGENT_PLANNER_CALL_RESERVE_USD", "0.002"
        ),
    )


def create_product_agent_app(
    *,
    product_settings: LocalAgentProductSettings | None = None,
    api_settings: InternalAgentApiSettings | None = None,
    planner_settings: DeepSeekPlannerSettings | None = None,
    durable_settings: DurableDeepSeekIntegrationSettings | None = None,
    queue_settings: PersistentAgentQueueSettings | None = None,
    supervisor_settings: WorkerSupervisorSettings | None = None,
    opener: Callable = urlopen,
):
    product = product_settings or load_local_agent_product_settings()
    api = api_settings or load_internal_agent_api_settings()
    planner = planner_settings or load_deepseek_planner_settings()
    durable = durable_settings or load_durable_deepseek_integration_settings()
    queue = queue_settings or load_persistent_agent_queue_settings()
    supervisor = supervisor_settings or load_worker_supervisor_settings()
    enabled = all(
        (
            product.enabled,
            api.enabled,
            planner.enabled,
            durable.planner.enabled,
            durable.worker.enabled,
            durable.agent.enabled,
            queue.enabled,
            supervisor.enabled,
        )
    )
    class DisabledProductPlanProvider:
        def propose(self, _request):
            raise RuntimeError("local_agent_product_disabled")

    provider = None
    if enabled:
        provider = DeepSeekProductPlanProvider(
            planner,
            budget_usd=product.planner_budget_usd,
            per_call_reserve_usd=product.per_call_reserve_usd,
            max_calls=product.max_planner_calls,
            opener=opener,
        )
    runtime = DisposableManagedAgentRuntime(
        settings=LocalAgentServiceSettings(
            enabled=enabled,
            lease_ttl_seconds=queue.lease_ttl_seconds,
            heartbeat_interval_ms=durable.worker.heartbeat_interval_ms,
            operation_timeout_ms=durable.worker.operation_timeout_ms,
            max_steps_per_run=durable.agent.max_steps_per_run,
            max_active_executions=supervisor.queue_capacity,
        ),
        api_settings=api,
        queue_settings=queue.model_copy(update={"enabled": enabled}),
        supervisor_settings=supervisor.model_copy(update={"enabled": enabled}),
        provider=provider or DisabledProductPlanProvider(),
    )

    @asynccontextmanager
    async def lifespan(_app):
        if enabled:
            runtime.start()
            runtime.supervisor.start()
        try:
            yield
        finally:
            runtime.close()

    app = create_internal_agent_app(
        settings=api,
        controller=runtime,
        lifespan=lifespan,
        require_loopback=True,
    )
    add_local_agent_console(app)
    app.state.product_agent_runtime = runtime
    app.state.product_planner = provider
    app.state.product_enabled = enabled
    return app


app = create_product_agent_app()


__all__ = [
    "LocalAgentProductSettings",
    "create_product_agent_app",
    "load_local_agent_product_settings",
]
