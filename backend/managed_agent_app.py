"""Default-off loopback Agent app with persistent queue Worker supervision."""

from contextlib import asynccontextmanager

from backend.agent_api.api import create_internal_agent_app
from backend.agent_api.auth import load_internal_agent_api_settings
from backend.agent_api.local_runtime import (
    DisposableManagedAgentRuntime,
    LocalAgentServiceSettings,
)
from backend.agent_api.models import AgentCaller
from backend.planning.task_queue import (
    PersistentAgentQueueSettings,
    load_persistent_agent_queue_settings,
)
from backend.planning.worker_supervisor import load_worker_supervisor_settings


def create_managed_agent_app():
    api_settings = load_internal_agent_api_settings()
    queue_settings = load_persistent_agent_queue_settings()
    supervisor_settings = load_worker_supervisor_settings()
    enabled = (
        api_settings.enabled
        and queue_settings.enabled
        and supervisor_settings.enabled
    )
    runtime = DisposableManagedAgentRuntime(
        settings=LocalAgentServiceSettings(
            enabled=enabled,
            lease_ttl_seconds=queue_settings.lease_ttl_seconds,
            max_active_executions=supervisor_settings.queue_capacity,
        ),
        api_settings=api_settings,
        queue_settings=PersistentAgentQueueSettings(
            enabled=enabled,
            lease_ttl_seconds=queue_settings.lease_ttl_seconds,
            poll_interval_ms=supervisor_settings.poll_interval_ms,
            max_tasks_per_drain=queue_settings.max_tasks_per_drain,
        ),
        supervisor_settings=supervisor_settings.model_copy(update={"enabled": enabled}),
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
        settings=api_settings,
        controller=runtime,
        lifespan=lifespan,
        require_loopback=True,
    )
    app.state.managed_agent_runtime = runtime
    app.state.agent_scope = AgentCaller().scope_key
    return app


app = create_managed_agent_app()
