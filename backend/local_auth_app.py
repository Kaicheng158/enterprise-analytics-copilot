"""Default-off, loopback-only authentication app for PV2-03a.

This surface is deliberately separate from the internal Agent API and Workspace
bridge. It is not mounted by the normal FastAPI application.
"""

from __future__ import annotations

from contextlib import contextmanager
from ipaddress import ip_address
import logging
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Cookie, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import psycopg

from backend.auth.models import AuthServiceError, LoginRequest, RegisterRequest, SessionView, WorkspaceIdentity
from backend.auth.service import LocalAuthService
from backend.auth.settings import LocalAuthSettings, load_local_auth_settings
from backend.auth.store import PostgresAuthStore


LOGGER = logging.getLogger("uvicorn.error")
WORKSPACE_DIR = Path(__file__).resolve().parents[1] / "frontend" / "workspace"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

SAFE_ERRORS = {
    "auth_disabled": (503, "Local authentication is not enabled"),
    "auth_registration_unavailable": (409, "Registration could not be completed"),
    "auth_invalid_credentials": (401, "Account or password is incorrect"),
    "auth_rate_limited": (429, "Too many attempts; try again later"),
    "auth_required": (401, "Authentication is required"),
    "auth_csrf_invalid": (403, "The request could not be verified"),
    "auth_storage_unavailable": (503, "Authentication service is unavailable"),
}


@contextmanager
def environment_connection():
    connection = psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ["POSTGRES_PORT"],
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        connect_timeout=5,
    )
    try:
        yield connection
    finally:
        connection.close()


def _error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": {"code": code, "message": message}})


def _safe_service_error(error: AuthServiceError) -> HTTPException:
    status_code, message = SAFE_ERRORS.get(error.code, (503, "Authentication request failed"))
    LOGGER.info("local_auth_event %s", {"status": "error", "code": error.code})
    return HTTPException(status_code=status_code, detail={"code": error.code, "message": message})


def create_local_auth_app(
    *,
    settings: LocalAuthSettings | None = None,
    service: LocalAuthService | None = None,
    connection_factory=None,
    mount_workspace: bool = True,
) -> FastAPI:
    auth_settings = settings or load_local_auth_settings()
    auth_service = service or LocalAuthService(
        PostgresAuthStore(connection_factory or environment_connection),
        settings=auth_settings,
    )
    expected = urlsplit(auth_settings.allowed_origin)
    expected_origin = f"{expected.scheme}://{expected.netloc}"

    app = FastAPI(title="Enterprise Analytics Copilot Local Authentication")

    @app.exception_handler(RequestValidationError)
    async def safe_validation_error(_request: Request, error: RequestValidationError):
        fields = sorted({str(item["loc"][-1]) for item in error.errors() if item.get("loc")})
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "auth_request_invalid",
                    "message": "Check the allowed account and password fields",
                    "fields": fields,
                }
            },
        )

    @app.middleware("http")
    async def local_security_boundary(request: Request, call_next):
        client_host = request.client.host if request.client else ""
        try:
            peer_is_loopback = ip_address(client_host).is_loopback
        except ValueError:
            peer_is_loopback = False
        try:
            host_is_loopback = ip_address(request.url.hostname or "").is_loopback
        except ValueError:
            host_is_loopback = (request.url.hostname or "").lower() == "localhost"
        if not peer_is_loopback or not host_is_loopback:
            return _error("auth_local_only", "Local authentication accepts loopback clients only", 403)
        if request.headers.get("host", "").lower() != expected.netloc.lower():
            return _error("auth_host_rejected", "Local authentication requires its configured Host", 403)
        if request.url.path.startswith("/auth-api/"):
            origin = request.headers.get("origin")
            fetch_site = request.headers.get("sec-fetch-site", "").lower()
            if fetch_site and fetch_site != "same-origin":
                return _error("auth_cross_site", "Authentication accepts same-origin requests only", 403)
            if origin is not None and origin.rstrip("/") != expected_origin:
                return _error("auth_cross_site", "Authentication accepts same-origin requests only", 403)
            if request.method not in SAFE_METHODS and origin is None:
                return _error("auth_origin_required", "Authentication writes require a same-origin Origin", 403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def cookie_token(value: str | None = Cookie(default=None, alias=auth_settings.cookie_name)) -> str | None:
        return value

    def set_session_cookie(response: Response, token: str) -> None:
        response.set_cookie(
            key=auth_settings.cookie_name,
            value=token,
            max_age=auth_settings.session_ttl_seconds,
            path="/",
            secure=auth_settings.cookie_secure,
            httponly=True,
            samesite=auth_settings.cookie_same_site,
        )

    @app.get("/auth-api/config")
    def auth_config():
        return {
            "response_version": "local-auth-config-v1",
            "enabled": auth_settings.enabled,
            "mode": "local-auth" if auth_settings.enabled else "demo-only",
            "account_format": "email",
            "password_min_length": 12,
            "recovery": "hold",
            "email_verification": "hold",
        }

    @app.post("/auth-api/register", response_model=SessionView, status_code=status.HTTP_201_CREATED)
    def register(request_body: RegisterRequest, request: Request, response: Response):
        try:
            view, token = auth_service.register(request_body, client_address=request.client.host)
        except AuthServiceError as error:
            raise _safe_service_error(error) from None
        set_session_cookie(response, token)
        LOGGER.info("local_auth_event %s", {"status": "success", "event": "register"})
        return view

    @app.post("/auth-api/login", response_model=SessionView)
    def login(request_body: LoginRequest, request: Request, response: Response):
        try:
            view, token = auth_service.login(request_body, client_address=request.client.host)
        except AuthServiceError as error:
            raise _safe_service_error(error) from None
        set_session_cookie(response, token)
        LOGGER.info("local_auth_event %s", {"status": "success", "event": "login"})
        return view

    @app.get("/auth-api/session", response_model=SessionView)
    def current_session(session_token: str | None = Cookie(default=None, alias=auth_settings.cookie_name)):
        try:
            return auth_service.current(session_token)
        except AuthServiceError as error:
            raise _safe_service_error(error) from None

    @app.post("/auth-api/logout", status_code=status.HTTP_204_NO_CONTENT)
    def logout(
        response: Response,
        session_token: str | None = Cookie(default=None, alias=auth_settings.cookie_name),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        try:
            auth_service.logout(session_token, csrf_token)
        except AuthServiceError as error:
            raise _safe_service_error(error) from None
        response.delete_cookie(
            key=auth_settings.cookie_name,
            path="/",
            secure=auth_settings.cookie_secure,
            httponly=True,
            samesite=auth_settings.cookie_same_site,
        )
        return None

    @app.get("/auth-api/my-workspace", response_model=WorkspaceIdentity)
    def my_workspace(session_token: str | None = Cookie(default=None, alias=auth_settings.cookie_name)):
        try:
            return auth_service.workspace_identity(session_token)
        except AuthServiceError as error:
            raise _safe_service_error(error) from None

    app.state.local_auth_enabled = auth_settings.enabled
    app.state.auth_service = auth_service
    app.state.agent_api_reused_as_identity = False
    if mount_workspace:
        app.mount("/", StaticFiles(directory=WORKSPACE_DIR, html=True), name="workspace")
    return app


app = create_local_auth_app()


__all__ = ["create_local_auth_app"]
