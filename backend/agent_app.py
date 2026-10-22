"""Independent, default-off entrypoint for the internal Agent API."""

from backend.agent_api import create_internal_agent_app


app = create_internal_agent_app()
