"""Default-off localhost bridge for the Phase 5.8b Workspace Demo."""

from __future__ import annotations

from contextlib import asynccontextmanager
from ipaddress import ip_address
import logging
import os
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.agent_api.api import IDEMPOTENCY_KEY_RE, SAFE_SERVICE_ERRORS
from backend.agent_api.auth import (
    InternalAgentApiSettings,
    InternalAgentAuthorizer,
    load_internal_agent_api_settings,
)
from backend.agent_api.local_runtime import (
    DisposableManagedAgentRuntime,
    LocalAgentServiceSettings,
)
from backend.agent_api.models import AgentTaskRequest, AgentTaskResponse
from backend.agent_api.service import AgentTaskServiceError
from backend.planning.task_queue import (
    PersistentAgentQueueSettings,
    load_persistent_agent_queue_settings,
)
from backend.planning.worker_supervisor import (
    WorkerSupervisorSettings,
    load_worker_supervisor_settings,
)
from backend.tools.models import StrictModel


LOGGER = logging.getLogger("uvicorn.error")
WORKSPACE_DIR = Path(__file__).resolve().parents[1] / "frontend" / "workspace"
SAFE_METHODS = frozenset({"GET", "HEAD"})


class WorkspaceBridgeSettings(StrictModel):
    enabled: bool = False


def load_workspace_bridge_settings() -> WorkspaceBridgeSettings:
    raw = os.getenv("WORKSPACE_AGENT_BRIDGE_ENABLED", "false").strip().lower()
    values = {"true": True, "false": False, "1": True, "0": False}
    if raw not in values:
        raise ValueError("Invalid WORKSPACE_AGENT_BRIDGE_ENABLED value")
    return WorkspaceBridgeSettings(enabled=values[raw])


def _safe_service_error(error: AgentTaskServiceError) -> HTTPException:
    code = error.code if error.code in SAFE_SERVICE_ERRORS else "agent_request_failed"
    status_code, message = SAFE_SERVICE_ERRORS.get(
        code, (503, "Workspace Agent request could not be completed")
    )
    LOGGER.info("workspace_agent_request %s", {"status": "error", "code": code})
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def _is_loopback_hostname(hostname: str | None) -> bool:
    if not hostname:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ip_address(hostname).is_loopback
    except ValueError:
        return False


def _origin_identity(value: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            return None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None
    return parsed.scheme, parsed.hostname.lower(), port


def _bridge_error(code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={"detail": {"code": code, "message": message}},
    )


def create_workspace_bridge_app(
    *,
    bridge_settings: WorkspaceBridgeSettings | None = None,
    api_settings: InternalAgentApiSettings | None = None,
    queue_settings: PersistentAgentQueueSettings | None = None,
    supervisor_settings: WorkerSupervisorSettings | None = None,
    runtime: DisposableManagedAgentRuntime | None = None,
) -> FastAPI:
    bridge = bridge_settings or load_workspace_bridge_settings()
    api = api_settings or load_internal_agent_api_settings()
    queue = queue_settings or load_persistent_agent_queue_settings()
    supervisor = supervisor_settings or load_worker_supervisor_settings()
    enabled = bool(bridge.enabled and api.enabled and queue.enabled and supervisor.enabled)

    owned_runtime = runtime or DisposableManagedAgentRuntime(
        settings=LocalAgentServiceSettings(
            enabled=enabled,
            lease_ttl_seconds=queue.lease_ttl_seconds,
            max_active_executions=supervisor.queue_capacity,
        ),
        api_settings=api,
        queue_settings=queue.model_copy(update={"enabled": enabled}),
        supervisor_settings=supervisor.model_copy(update={"enabled": enabled}),
    )
    authorizer = InternalAgentAuthorizer(api)

    @asynccontextmanager
    async def lifespan(_app):
        if enabled:
            owned_runtime.start()
            owned_runtime.supervisor.start()
        try:
            yield
        finally:
            owned_runtime.close()

    app = FastAPI(
        title="Enterprise Analytics Copilot Workspace Bridge",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def localhost_only(request: Request, call_next):
        client_host = request.client.host if request.client is not None else ""
        try:
            allowed = ip_address(client_host).is_loopback
        except ValueError:
            allowed = False
        if not allowed:
            return _bridge_error(
                "workspace_bridge_local_only",
                "Workspace bridge accepts loopback clients only",
            )

        # Host is validated independently of the peer address to prevent DNS
        # rebinding from turning a remote origin into a trusted localhost app.
        if not _is_loopback_hostname(request.url.hostname):
            return _bridge_error(
                "workspace_bridge_host_rejected",
                "Workspace bridge requires a loopback Host",
            )

        if request.url.path.startswith("/workspace-api/"):
            expected_origin = _origin_identity(str(request.base_url).rstrip("/"))
            supplied_origin = request.headers.get("origin")
            fetch_site = request.headers.get("sec-fetch-site", "").lower()
            if fetch_site and fetch_site != "same-origin":
                return _bridge_error(
                    "workspace_bridge_cross_site",
                    "Workspace API accepts same-origin browser requests only",
                )
            if supplied_origin is not None and _origin_identity(supplied_origin) != expected_origin:
                return _bridge_error(
                    "workspace_bridge_cross_site",
                    "Workspace API accepts same-origin browser requests only",
                )
            if request.method not in SAFE_METHODS and supplied_origin is None:
                return _bridge_error(
                    "workspace_bridge_origin_required",
                    "Workspace API write requests require a same-origin Origin",
                )
        return await call_next(request)

    def require_enabled():
        if not enabled:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "workspace_bridge_disabled",
                    "message": "Workspace Agent bridge is not enabled",
                },
            )
        # The credential never leaves this process. The existing authorizer still
        # creates the fixed server-owned principal and scope used by the Agent API.
        return authorizer.authorize(api.api_key.get_secret_value())

    @app.get("/workspace-api/config")
    def bridge_config():
        return {
            "response_version": "workspace-bridge-config-v1",
            "enabled": enabled,
            "mode": "synthetic-sales-fake" if enabled else "disabled",
            "task": "compare_quarterly_sales",
        }

    @app.post(
        "/workspace-api/tasks",
        response_model=AgentTaskResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def submit_task(
        request_body: AgentTaskRequest,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> AgentTaskResponse:
        caller = require_enabled()
        if idempotency_key is None or not IDEMPOTENCY_KEY_RE.fullmatch(idempotency_key):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "agent_idempotency_key_invalid",
                    "message": "A valid Idempotency-Key header is required",
                },
            )
        try:
            response = owned_runtime.submit(
                request_body,
                idempotency_key=idempotency_key,
                caller=caller,
            )
        except AgentTaskServiceError as error:
            raise _safe_service_error(error) from None
        LOGGER.info(
            "workspace_agent_request %s",
            {
                "status": "accepted",
                "task_id": str(response.task_id),
                "task_status": response.status,
                "created": response.created,
            },
        )
        return response

    @app.get(
        "/workspace-api/tasks/{task_id}",
        response_model=AgentTaskResponse,
    )
    def get_task(task_id: UUID) -> AgentTaskResponse:
        caller = require_enabled()
        try:
            response = owned_runtime.get(task_id, caller=caller)
        except AgentTaskServiceError as error:
            raise _safe_service_error(error) from None
        return response

    app.state.workspace_bridge_enabled = enabled
    app.state.workspace_bridge_runtime = owned_runtime
    app.state.internal_agent_key_exposed = False
    app.mount("/", StaticFiles(directory=WORKSPACE_DIR, html=True), name="workspace")
    return app


app = create_workspace_bridge_app()


__all__ = [
    "WorkspaceBridgeSettings",
    "create_workspace_bridge_app",
    "load_workspace_bridge_settings",
]
