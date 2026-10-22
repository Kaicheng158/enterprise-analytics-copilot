"""Separate default-off internal Agent HTTP surface."""

from __future__ import annotations

import logging
import re
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, status

from .auth import (
    AgentAuthorizationError,
    InternalAgentApiSettings,
    InternalAgentAuthorizer,
    load_internal_agent_api_settings,
)
from .models import AgentCaller, AgentTaskRequest, AgentTaskResponse
from .service import (
    AgentTaskController,
    AgentTaskServiceError,
    UnavailableAgentTaskController,
)


LOGGER = logging.getLogger("uvicorn.error")
IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$")
SAFE_SERVICE_ERRORS = {
    "agent_runtime_unavailable": (503, "Internal Agent runtime is not configured"),
    "agent_idempotency_conflict": (409, "Idempotency key is already bound to another request"),
    "agent_plan_rejected": (502, "Planner did not produce an executable authorized plan"),
    "agent_persistence_unavailable": (503, "Agent persistence is unavailable"),
    "agent_task_not_found": (404, "Agent task was not found"),
    "agent_forbidden": (403, "Agent caller is not authorized for this scope"),
    "agent_state_integrity_error": (503, "Agent task state failed integrity validation"),
    "agent_status_changed": (409, "Agent task changed while status was read; retry the request"),
}


def create_internal_agent_app(
    *,
    settings: InternalAgentApiSettings | None = None,
    controller: AgentTaskController | None = None,
) -> FastAPI:
    configured = settings or load_internal_agent_api_settings()
    authorizer = InternalAgentAuthorizer(configured)
    tasks = controller or UnavailableAgentTaskController()
    app = FastAPI(title="Enterprise Analytics Copilot Internal Agent API")

    def authorize(presented_key: str | None) -> AgentCaller:
        try:
            return authorizer.authorize(presented_key)
        except AgentAuthorizationError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail={"code": error.code, "message": str(error)},
            ) from None

    def service_error(error: AgentTaskServiceError) -> HTTPException:
        code = error.code if error.code in SAFE_SERVICE_ERRORS else "agent_request_failed"
        status_code, message = SAFE_SERVICE_ERRORS.get(
            code, (503, "Internal Agent request could not be completed")
        )
        LOGGER.info(
            "internal_agent_request %s",
            {"status": "error", "code": code},
        )
        return HTTPException(
            status_code=status_code,
            detail={"code": code, "message": message},
        )

    @app.post(
        "/internal/agent/tasks",
        response_model=AgentTaskResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def submit_task(
        request: AgentTaskRequest,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
        internal_key: Annotated[
            str | None, Header(alias="X-Internal-Agent-Key")
        ] = None,
    ) -> AgentTaskResponse:
        caller = authorize(internal_key)
        if idempotency_key is None or not IDEMPOTENCY_KEY_RE.fullmatch(idempotency_key):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "agent_idempotency_key_invalid",
                    "message": "A valid Idempotency-Key header is required",
                },
            )
        try:
            response = tasks.submit(
                request,
                idempotency_key=idempotency_key,
                caller=caller,
            )
        except AgentTaskServiceError as error:
            raise service_error(error) from None
        LOGGER.info(
            "internal_agent_request %s",
            {
                "status": "accepted",
                "task_id": str(response.task_id),
                "task_status": response.status,
                "created": response.created,
            },
        )
        return response

    @app.get(
        "/internal/agent/tasks/{task_id}",
        response_model=AgentTaskResponse,
    )
    def get_task(
        task_id: UUID,
        internal_key: Annotated[
            str | None, Header(alias="X-Internal-Agent-Key")
        ] = None,
    ) -> AgentTaskResponse:
        caller = authorize(internal_key)
        try:
            response = tasks.get(task_id, caller=caller)
        except AgentTaskServiceError as error:
            raise service_error(error) from None
        LOGGER.info(
            "internal_agent_request %s",
            {
                "status": "success",
                "task_id": str(response.task_id),
                "task_status": response.status,
            },
        )
        return response

    return app
