"""Default-off, loopback-only Product Facade for PV2-04a/PV2-04b.

The app reuses the PV2-03a authentication routes and static Workspace. PV2-04a
keeps its metadata/analysis-contract path; the separately gated PV2-04b path
adds bounded private Dataset upload and redacted preview. Neither path creates
an analysis execution or exposes SQL, Tool or model authority.
"""

from __future__ import annotations

import base64
import binascii
from contextlib import contextmanager
import logging
import os
from pathlib import Path
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
from backend.datasets import DatasetPersistenceSettings, PrivateDatasetPersistenceService
from backend.datasets.private_storage import PrivateLocalBlobStore
from backend.local_auth_app import SAFE_ERRORS as AUTH_SAFE_ERRORS
from backend.local_auth_app import WORKSPACE_DIR, create_local_auth_app
from backend.product_facade.models import (
    AnalysisContractValidationResponse,
    CreatePrivateProjectRequest,
    CreatePrivateProjectRequestV2,
    DatasetListResponse,
    DatasetPreviewResponse,
    DatasetRevisionView,
    DatasetUploadMetadata,
    DatasetAnalysisTaskRequestV1,
    ProductDatasetSettings,
    ProductFacadeFailure,
    ProductFacadeSettings,
    ProductProjectView,
    ProductWorkspaceView,
    ProductProjectViewV2,
    ProductWorkspaceViewV2,
)
from backend.product_facade.datasets import (
    AuthorizedDatasetProductService,
    DatasetValidationRequired,
)
from backend.product_facade.service import ALLOWED_OPERATIONS, LocalProductFacadeService
from backend.product_facade.settings import (
    load_product_dataset_settings,
    load_product_facade_settings,
    validate_product_dataset_settings,
    validate_product_facade_settings,
)


LOGGER = logging.getLogger("uvicorn.error")
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
PRODUCT_UPLOAD_DIR = Path(__file__).resolve().parents[1] / "frontend" / "product-upload"

PRODUCT_SAFE_ERRORS = {
    "product_facade_disabled": (503, "Local product access is not enabled"),
    "product_storage_unavailable": (503, "Product data is temporarily unavailable"),
    "product_contract_invalid": (422, "The product request is invalid"),
    "product_resource_unavailable": (404, "The requested resource is unavailable"),
    "product_authorization_changed": (409, "Authorization changed; refresh before continuing"),
    "product_dataset_upload_disabled": (503, "Local Dataset upload is not enabled"),
    "product_idempotency_invalid": (422, "A valid Idempotency-Key is required"),
    "product_idempotency_conflict": (409, "The Idempotency-Key was already used for another request"),
    "product_file_limit": (413, "The file exceeds the local upload limit"),
    "product_file_invalid": (422, "The file could not be safely validated"),
    "product_quota_exceeded": (429, "The private Project storage quota is exceeded"),
    "product_reconciliation_required": (503, "The storage result requires reconciliation"),
    "product_contract_deprecated": (409, "Use the versioned idempotent Project write contract"),
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
    dataset_settings: ProductDatasetSettings | None = None,
    auth_settings: LocalAuthSettings | None = None,
    authorization_settings: ProjectAuthorizationSettings | None = None,
    auth_service: LocalAuthService | None = None,
    authorization_service: ProjectAuthorizationService | None = None,
    product_service: LocalProductFacadeService | None = None,
    dataset_service: AuthorizedDatasetProductService | None = None,
    connection_factory=None,
) -> FastAPI:
    facade_settings = validate_product_facade_settings(
        product_settings or load_product_facade_settings()
    )
    local_dataset_settings = validate_product_dataset_settings(
        dataset_settings or load_product_dataset_settings()
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
    local_dataset_service = dataset_service
    if local_dataset_service is None and local_dataset_settings.enabled:
        blob_store = PrivateLocalBlobStore(Path(local_dataset_settings.storage_root))
        persistence = PrivateDatasetPersistenceService(
            connections,
            blob_store,
            settings=DatasetPersistenceSettings(enabled=True),
        )
        local_dataset_service = AuthorizedDatasetProductService(
            connections,
            project_authorization,
            persistence,
            facade_settings=facade_settings,
            dataset_settings=local_dataset_settings,
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
            "dataset_upload": local_dataset_settings.enabled,
            "analysis_execution": False,
            "public_access": False,
        }

    def require_dataset_service() -> AuthorizedDatasetProductService:
        if not local_dataset_settings.enabled or local_dataset_service is None:
            raise _service_error(ProductFacadeFailure("product_dataset_upload_disabled"))
        return local_dataset_service

    @app.get("/product-api/v2/workspace", response_model=ProductWorkspaceViewV2)
    def product_workspace_v2(
        session_token: str | None = Cookie(default=None, alias=local_auth_settings.cookie_name),
    ):
        principal = read_principal(session_token)
        try:
            return require_dataset_service().workspace(principal)
        except (AuthorizationFailure, ProductFacadeFailure) as error:
            raise _service_error(error) from None

    @app.post(
        "/product-api/v2/projects",
        response_model=ProductProjectViewV2,
        status_code=status.HTTP_201_CREATED,
    )
    def create_project_v2(
        request_body: CreatePrivateProjectRequestV2,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        session_token: str | None = Cookie(default=None, alias=local_auth_settings.cookie_name),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        principal = write_principal(session_token, csrf_token)
        try:
            return require_dataset_service().create_project(
                principal, request_body, idempotency_key=idempotency_key or ""
            )
        except (AuthorizationFailure, ProductFacadeFailure) as error:
            raise _service_error(error) from None

    @app.get("/product-api/v2/projects/{project_id}", response_model=ProductProjectViewV2)
    def get_project_v2(
        project_id: UUID,
        session_token: str | None = Cookie(default=None, alias=local_auth_settings.cookie_name),
    ):
        principal = read_principal(session_token)
        try:
            return require_dataset_service().get_project(principal, project_id)
        except (AuthorizationFailure, ProductFacadeFailure) as error:
            raise _service_error(error) from None

    @app.post(
        "/product-api/v2/projects/{project_id}/datasets",
        response_model=DatasetRevisionView,
        status_code=status.HTTP_201_CREATED,
    )
    async def upload_dataset_v2(
        project_id: UUID,
        request: Request,
        metadata_json: str | None = Header(default=None, alias="X-Dataset-Metadata"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        session_token: str | None = Cookie(default=None, alias=local_auth_settings.cookie_name),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        principal = write_principal(session_token, csrf_token)
        service = require_dataset_service()
        if (
            metadata_json is None
            or not metadata_json.isascii()
            or len(metadata_json) > local_dataset_settings.max_schema_header_bytes * 2
        ):
            raise _service_error(ProductFacadeFailure("product_contract_invalid"))
        try:
            padded = metadata_json + "=" * (-len(metadata_json) % 4)
            decoded_metadata = base64.b64decode(padded, altchars=b"-_", validate=True)
            if len(decoded_metadata) > local_dataset_settings.max_schema_header_bytes:
                raise ValueError
            metadata = DatasetUploadMetadata.model_validate_json(decoded_metadata)
        except (binascii.Error, UnicodeError, ValueError):
            raise _service_error(ProductFacadeFailure("product_contract_invalid")) from None
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        allowed_content_types = {
            ".csv": {"text/csv", "application/csv", "application/octet-stream"},
            ".xlsx": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/octet-stream",
            },
        }
        suffix = ".xlsx" if metadata.filename.endswith(".xlsx") else ".csv"
        if content_type not in allowed_content_types[suffix]:
            raise _service_error(ProductFacadeFailure("product_file_invalid"))
        raw_length = request.headers.get("content-length")
        if raw_length is not None:
            try:
                if int(raw_length) < 1 or int(raw_length) > local_dataset_settings.max_file_bytes:
                    raise _service_error(ProductFacadeFailure("product_file_limit"))
            except ValueError:
                raise _service_error(ProductFacadeFailure("product_contract_invalid")) from None
        source = bytearray()
        async for chunk in request.stream():
            source.extend(chunk)
            if len(source) > local_dataset_settings.max_file_bytes:
                raise _service_error(ProductFacadeFailure("product_file_limit"))
        try:
            return service.upload(
                principal,
                project_id,
                metadata,
                bytes(source),
                idempotency_key=idempotency_key or "",
            )
        except DatasetValidationRequired as error:
            return JSONResponse(
                status_code=422,
                content={
                    "detail": {
                        "code": "dataset_validation_required",
                        "message": "Confirm or correct the reported Dataset quality issues",
                        "quality": error.quality.model_dump(mode="json"),
                    }
                },
            )
        except (AuthorizationFailure, ProductFacadeFailure) as error:
            raise _service_error(error) from None

    @app.get(
        "/product-api/v2/projects/{project_id}/datasets",
        response_model=DatasetListResponse,
    )
    def list_datasets_v2(
        project_id: UUID,
        session_token: str | None = Cookie(default=None, alias=local_auth_settings.cookie_name),
    ):
        principal = read_principal(session_token)
        try:
            return require_dataset_service().list_revisions(principal, project_id)
        except (AuthorizationFailure, ProductFacadeFailure) as error:
            raise _service_error(error) from None

    @app.get(
        "/product-api/v2/projects/{project_id}/datasets/{dataset_revision_id}",
        response_model=DatasetRevisionView,
    )
    def get_dataset_revision_v2(
        project_id: UUID,
        dataset_revision_id: UUID,
        session_token: str | None = Cookie(default=None, alias=local_auth_settings.cookie_name),
    ):
        principal = read_principal(session_token)
        try:
            return require_dataset_service().get_revision(
                principal, project_id, dataset_revision_id
            )
        except (AuthorizationFailure, ProductFacadeFailure) as error:
            raise _service_error(error) from None

    @app.get(
        "/product-api/v2/projects/{project_id}/datasets/{dataset_revision_id}/preview",
        response_model=DatasetPreviewResponse,
    )
    def preview_dataset_v2(
        project_id: UUID,
        dataset_revision_id: UUID,
        session_token: str | None = Cookie(default=None, alias=local_auth_settings.cookie_name),
    ):
        principal = read_principal(session_token)
        try:
            return require_dataset_service().preview(
                principal, project_id, dataset_revision_id
            )
        except (AuthorizationFailure, ProductFacadeFailure) as error:
            raise _service_error(error) from None

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
            if local_dataset_settings.enabled:
                raise ProductFacadeFailure("product_contract_deprecated")
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
    app.state.product_dataset_service = local_dataset_service
    app.state.dataset_upload_enabled = local_dataset_settings.enabled
    if local_dataset_settings.enabled:
        app.mount(
            "/dataset-upload",
            StaticFiles(directory=PRODUCT_UPLOAD_DIR, html=True),
            name="dataset-upload",
        )
    app.mount("/", StaticFiles(directory=WORKSPACE_DIR, html=True), name="workspace")
    return app


app = create_local_product_facade_app()


__all__ = ["create_local_product_facade_app"]
