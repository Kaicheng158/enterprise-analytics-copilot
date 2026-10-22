"""Default-off localhost-only Agent integration service."""

from contextlib import asynccontextmanager

from backend.agent_api.api import create_internal_agent_app
from backend.agent_api.auth import load_internal_agent_api_settings
from backend.agent_api.local_runtime import (
    DisposableLocalAgentRuntime,
    load_local_agent_service_settings,
)


def create_local_agent_app():
    api_settings = load_internal_agent_api_settings()
    local_settings = load_local_agent_service_settings()
    runtime = DisposableLocalAgentRuntime(
        settings=local_settings,
        api_settings=api_settings,
    )

    @asynccontextmanager
    async def lifespan(_app):
        if local_settings.enabled:
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
    app.state.local_agent_runtime = runtime
    return app


app = create_local_agent_app()
