"""Default-off, loopback-only Product Facade for PV2-04a.

The app reuses the PV2-03a authentication routes and static Workspace while
adding only private Project metadata and Dataset analysis-contract validation.
It creates no analysis execution and exposes no upload, SQL, Tool or model API.
"""

from __future__ import annotations

from contextlib import contextmanager
import logging
import os
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Cookie, FastAPI, Header, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import psycopg

from backend.auth.models import AuthServiceError
from backend.auth.service import LocalAuthService
from backend.auth.settings import LocalAuthSettings, load_local_auth_settings
from backend.auth.store import PostgresAuthStore
from backend.authorization import (
    AuthorizationFailure,
    ProjectAuthorizationService,
    ProjectAuthorizationSettings,
    load_project_authorization_settings,
)
from backend.local_auth_app import SAFE_ERRORS as AUTH_SAFE_ERRORS
from backend.local_auth_app import WORKSPACE_DIR, create_local_auth_app
from backend.product_facade.models import (
    AnalysisContractValidationResponse,
    CreatePrivateProjectRequest,
    DatasetAnalysisTaskRequestV1,
    ProductFacadeFailure,
    ProductFacadeSettings,
    ProductProjectView,
    ProductWorkspaceView,
)
from backend.product_facade.service import ALLOWED_OPERATIONS, LocalProductFacadeService
from backend.product_facade.settings import load_product_facade_settings, validate_product_facade_settings


LOGGER = logging.getLogger("uvicorn.error")
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

PRODUCT_SAFE_ERRORS = {
    "product_facade_disabled": (503, "Local product access is not enabled"),
    "product_storage_unavailable": (503, "Product data is temporarily unavailable"),
    "product_contract_invalid": (422, "The product request is invalid"),
    "product_resource_unavailable": (404, "The requested resource is unavailable"),
    "product_authorization_changed": (409, "Authorization changed; refresh before continuing"),
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
    return JSONResponse(
        status_code=status_code,
        content={"detail": {"code": code, "message": message}},
    )


def _service_error(error: Exception) -> HTTPException:
    if isinstance(error, AuthServiceError):
        status_code, message = AUTH_SAFE_ERRORS.get(
            error.code, (503, "Authentication request failed")
        )
        code = error.code
    elif isinstance(error, AuthorizationFailure):
        if error.code == "authorization_disabled":
            status_code, code, message = 503, "product_authorization_disabled", "Product authorization is not enabled"
        elif error.code == "authorization_unavailable":
            status_code, code, message = 503, "product_authorization_unavailable", "Product authorization is unavailable"
        elif error.code == "authorization_contract_invalid":
            status_code, code, message = 422, "product_request_invalid", "The product request is invalid"
        else:
            status_code, code, message = 404, "product_resource_unavailable", "The requested resource is unavailable"
    elif isinstance(error, ProductFacadeFailure):
        status_code, message = PRODUCT_SAFE_ERRORS.get(
            error.code, (503, "Product request failed")
        )
        code = error.code
    else:  # pragma: no cover - callers only pass bounded service errors
        status_code, code, message = 503, "product_request_failed", "Product request failed"
    LOGGER.info("local_product_event %s", {"status": "error", "code": code})
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def create_local_product_facade_app(
    *,
    product_settings: ProductFacadeSettings | None = None,
    auth_settings: LocalAuthSettings | None = None,
    authorization_settings: ProjectAuthorizationSettings | None = None,
    auth_service: LocalAuthService | None = None,
    authorization_service: ProjectAuthorizationService | None = None,
    product_service: LocalProductFacadeService | None = None,
    connection_factory=None,
) -> FastAPI:
    facade_settings = validate_product_facade_settings(
        product_settings or load_product_facade_settings()
    )
    local_auth_settings = auth_settings or load_local_auth_settings()
    if local_auth_settings.allowed_origin.rstrip("/") != facade_settings.allowed_origin.rstrip("/"):
        raise ValueError("Product Facade and authentication origins must match")
    connections = connection_factory or environment_connection
    local_auth_service = auth_service or LocalAuthService(
        PostgresAuthStore(connections), settings=local_auth_settings
    )
    project_authorization = authorization_service or ProjectAuthorizationService(
        connections,
        settings=authorization_settings or load_project_authorization_settings(),
    )
    local_product_service = product_service or LocalProductFacadeService(
        connections,
        project_authorization,
        settings=facade_settings,
    )

    app = create_local_auth_app(
        settings=local_auth_settings,
        service=local_auth_service,
        connection_factory=connections,
        mount_workspace=False,
    )
    app.title = "Enterprise Analytics Copilot Local Product Facade"
    expected = urlsplit(facade_settings.allowed_origin)
    expected_origin = f"{expected.scheme}://{expected.netloc}"

    @app.exception_handler(RequestValidationError)
    async def safe_validation_error(request: Request, error: RequestValidationError):
        if not request.url.path.startswith("/product-api/"):
            fields = sorted({str(item["loc"][-1]) for item in error.errors() if item.get("loc")})
            return _error(
                "auth_request_invalid",
                "Check the allowed account and password fields",
                422,
            ) if not fields else JSONResponse(
                status_code=422,
                content={"detail": {"code": "auth_request_invalid", "message": "Check the allowed account and password fields", "fields": fields}},
            )
        fields = sorted(
            {
                str(item["loc"][-1])
                for item in error.errors()
                if item.get("loc") and isinstance(item["loc"][-1], (str, int))
            }
        )
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "product_request_invalid",
                    "message": "Check the allowed product request fields",
                    "fields": fields,
                }
            },
        )

    @app.middleware("http")
    async def product_same_origin_boundary(request: Request, call_next):
        if request.url.path.startswith("/product-api/"):
            origin = request.headers.get("origin")
            fetch_site = request.headers.get("sec-fetch-site", "").lower()
            if fetch_site and fetch_site != "same-origin":
                return _error("product_cross_site", "Product access accepts same-origin requests only", 403)
            if origin is not None and origin.rstrip("/") != expected_origin:
                return _error("product_cross_site", "Product access accepts same-origin requests only", 403)
            if request.method not in SAFE_METHODS and origin is None:
                return _error("product_origin_required", "Product writes require a same-origin Origin", 403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def read_principal(session_token: str | None):
        try:
            principal, _ = local_auth_service.require_principal(session_token)
            return principal
        except AuthServiceError as error:
            raise _service_error(error) from None

    def write_principal(session_token: str | None, csrf_token: str | None):
        try:
            return local_auth_service.require_write_principal(session_token, csrf_token)
        except AuthServiceError as error:
            raise _service_error(error) from None

    @app.get("/product-api/config")
    def product_config():
        return {
            "response_version": "local-product-config-v1",
            "enabled": facade_settings.enabled,
            "policy_version": facade_settings.policy_version,
            "allowed_operations": ALLOWED_OPERATIONS,
            "dataset_upload": False,
            "analysis_execution": False,
            "public_access": False,
        }

    @app.get("/product-api/workspace", response_model=ProductWorkspaceView)
    def product_workspace(
        session_token: str | None = Cookie(default=None, alias=local_auth_settings.cookie_name),
    ):
        principal = read_principal(session_token)
        try:
            return local_product_service.workspace(principal)
        except (AuthorizationFailure, ProductFacadeFailure) as error:
            raise _service_error(error) from None

    @app.post(
        "/product-api/projects",
        response_model=ProductProjectView,
        status_code=status.HTTP_201_CREATED,
    )
    def create_project(
        _request_body: CreatePrivateProjectRequest,
        session_token: str | None = Cookie(default=None, alias=local_auth_settings.cookie_name),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        principal = write_principal(session_token, csrf_token)
        try:
            return local_product_service.create_private_project(principal)
        except (AuthorizationFailure, ProductFacadeFailure) as error:
            raise _service_error(error) from None

    @app.get("/product-api/projects/{project_id}", response_model=ProductProjectView)
    def get_project(
        project_id: UUID,
        session_token: str | None = Cookie(default=None, alias=local_auth_settings.cookie_name),
    ):
        principal = read_principal(session_token)
        try:
            return local_product_service.get_project(principal, project_id)
        except (AuthorizationFailure, ProductFacadeFailure) as error:
            raise _service_error(error) from None

    @app.post(
        "/product-api/projects/{project_id}/analysis-contracts/validate",
        response_model=AnalysisContractValidationResponse,
    )
    def validate_analysis_contract(
        project_id: UUID,
        request_body: DatasetAnalysisTaskRequestV1,
        session_token: str | None = Cookie(default=None, alias=local_auth_settings.cookie_name),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        principal = write_principal(session_token, csrf_token)
        try:
            return local_product_service.validate_analysis_request(
                principal, project_id, request_body
            )
        except (AuthorizationFailure, ProductFacadeFailure) as error:
            raise _service_error(error) from None

    app.state.product_facade_enabled = facade_settings.enabled
    app.state.product_facade_policy_version = facade_settings.policy_version
    app.state.product_service = local_product_service
    app.state.analysis_execution_enabled = False
    app.state.dataset_upload_enabled = False
    app.mount("/", StaticFiles(directory=WORKSPACE_DIR, html=True), name="workspace")
    return app


app = create_local_product_facade_app()


__all__ = ["create_local_product_facade_app"]
