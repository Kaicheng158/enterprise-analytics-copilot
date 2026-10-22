"""Default-off localhost task-submission app for the persistent Agent queue."""

from contextlib import asynccontextmanager

from backend.agent_api.api import create_internal_agent_app
from backend.agent_api.auth import load_internal_agent_api_settings
from backend.agent_api.local_runtime import (
    DisposableQueuedAgentRuntime,
    LocalAgentServiceSettings,
)
from backend.planning.task_queue import load_persistent_agent_queue_settings


def create_queued_agent_app():
    api_settings = load_internal_agent_api_settings()
    queue_settings = load_persistent_agent_queue_settings()
    runtime = DisposableQueuedAgentRuntime(
        settings=LocalAgentServiceSettings(
            enabled=queue_settings.enabled,
            lease_ttl_seconds=queue_settings.lease_ttl_seconds,
        ),
        api_settings=api_settings,
        queue_settings=queue_settings,
    )

    @asynccontextmanager
    async def lifespan(_app):
        if queue_settings.enabled:
            runtime.start()
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
    app.state.queued_agent_runtime = runtime
    return app


app = create_queued_agent_app()
