"""Trusted composition of session identity, project authorization and Dataset metadata."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Callable
from uuid import UUID, uuid4

import psycopg

from backend.auth.models import AuthPrincipal
from backend.authorization import ProjectAuthorizationService

from .models import (
    AnalysisContractValidationResponse,
    AnalysisContextV1,
    AuthorizedDatasetRevision,
    ClarificationBundle,
    ClarificationQuestion,
    DatasetAnalysisTaskRequestV1,
    ProductCapabilities,
    ProductFacadeFailure,
    ProductFacadeSettings,
    ProductProjectView,
    ProductWorkspaceView,
)


ALLOWED_OPERATIONS = ("profile", "aggregate", "compare", "trend")


class LocalProductFacadeService:
    """Metadata-only local product boundary; it never reads private Dataset blobs."""

    def __init__(
        self,
        connection_factory: Callable,
        authorization: ProjectAuthorizationService,
        *,
        settings: ProductFacadeSettings | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self._connections = connection_factory
        self._authorization = authorization
        self._settings = settings or ProductFacadeSettings()
        self._clock = clock or (lambda: datetime.now(UTC))

    def _enabled(self) -> None:
        if not self._settings.enabled:
            raise ProductFacadeFailure("product_facade_disabled")

    @contextmanager
    def _connection(self):
        try:
            with self._connections() as connection:
                yield connection
        except ProductFacadeFailure:
            raise
        except (psycopg.Error, KeyError, OSError):
            raise ProductFacadeFailure("product_storage_unavailable") from None

    def create_private_project(self, principal: AuthPrincipal) -> ProductProjectView:
        self._enabled()
        project_id = uuid4()
        owner = self._authorization.create_owned_project(
            principal,
            project_id=project_id,
            quota_bytes=self._settings.default_project_quota_bytes,
            lifecycle_policy_version=self._settings.lifecycle_policy_version,
        )
        return self._project_view(principal, owner.project_id, owner.authorization_version)

    def workspace(self, principal: AuthPrincipal) -> ProductWorkspaceView:
        self._enabled()
        if type(principal) is not AuthPrincipal:
            raise ProductFacadeFailure("product_contract_invalid")
        now = self._clock()
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT project.project_id,owner.authorization_version,
                          project.quota_bytes,project.used_bytes,project.reserved_bytes,
                          project.lifecycle_policy_version
                   FROM product_auth.project_owners owner
                   JOIN product_auth.users users ON users.user_id=owner.owner_user_id
                   JOIN product_auth.sessions sessions
                     ON sessions.user_id=users.user_id AND sessions.session_id=%s
                   JOIN dataset_store.projects project ON project.project_id=owner.project_id
                   WHERE owner.owner_user_id=%s AND owner.state='active'
                     AND users.state='active' AND sessions.state='active'
                     AND sessions.expires_at>%s AND project.state='active'
                   ORDER BY project.created_at,project.project_id""",
                (principal.session_id, principal.user_id, now),
            ).fetchall()
        projects = tuple(self._row_to_project(row) for row in rows)
        return ProductWorkspaceView(projects=projects)

    def get_project(self, principal: AuthPrincipal, project_id: UUID) -> ProductProjectView:
        self._enabled()
        authorized = self._authorization.require_project(
            principal, project_id, action="project_read"
        )
        return self._project_view(principal, project_id, authorized.authorization_version)

    def validate_analysis_request(
        self,
        principal: AuthPrincipal,
        project_id: UUID,
        request: DatasetAnalysisTaskRequestV1,
    ) -> AnalysisContractValidationResponse:
        """Authorize immutable revision metadata and validate the future task contract.

        No execution, Tool call, blob read, chart or report is created here.
        """
        self._enabled()
        if type(request) is not DatasetAnalysisTaskRequestV1:
            raise ProductFacadeFailure("product_contract_invalid")
        project_access = self._authorization.require_project(
            principal, project_id, action="project_read"
        )
        revisions: list[AuthorizedDatasetRevision] = []
        for binding in request.datasets:
            self._authorization.require_asset(
                principal, project_id, binding.asset_id, action="asset_read"
            )
            revisions.append(self._ready_revision(project_id, binding))

        # Recheck after metadata lookup so a concurrent owner revocation cannot
        # return a successfully validated context from a stale authorization.
        final_access = self._authorization.require_project(
            principal, project_id, action="project_read"
        )
        if final_access.authorization_version != project_access.authorization_version:
            raise ProductFacadeFailure("product_authorization_changed")

        clarification = self._clarification(request)
        context = AnalysisContextV1(
            project_id=project_id,
            authorization_version=final_access.authorization_version,
            datasets=tuple(revisions),
            allowed_operations=ALLOWED_OPERATIONS,
        )
        return AnalysisContractValidationResponse(
            status="clarification_required" if clarification else "contract_validated",
            request=request,
            context=context,
            clarification=clarification,
        )

    def _ready_revision(self, project_id, binding) -> AuthorizedDatasetRevision:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT revision.source_format,revision.source_sha256,
                          revision.normalized_sha256,dataset.dataset_contract_version,
                          dataset.parser_policy_version
                   FROM dataset_store.assets asset
                   JOIN dataset_store.asset_revisions revision
                     ON (revision.project_id,revision.asset_id)=(asset.project_id,asset.asset_id)
                   JOIN dataset_store.dataset_revisions dataset
                     ON (dataset.project_id,dataset.asset_revision_id)=
                        (revision.project_id,revision.revision_id)
                   WHERE asset.project_id=%s AND asset.asset_id=%s AND asset.state='active'
                     AND revision.revision_id=%s AND revision.state='ready'
                     AND dataset.dataset_revision_id=%s AND dataset.state='ready'""",
                (
                    project_id,
                    binding.asset_id,
                    binding.asset_revision_id,
                    binding.dataset_revision_id,
                ),
            ).fetchone()
        if row is None:
            raise ProductFacadeFailure("product_resource_unavailable")
        return AuthorizedDatasetRevision(
            project_id=project_id,
            asset_id=binding.asset_id,
            asset_revision_id=binding.asset_revision_id,
            dataset_revision_id=binding.dataset_revision_id,
            source_format=row[0],
            source_sha256=row[1],
            normalized_sha256=row[2],
            dataset_contract_version=row[3],
            parser_policy_version=row[4],
        )

    def _project_view(
        self,
        principal: AuthPrincipal,
        project_id: UUID,
        authorization_version: int,
    ) -> ProductProjectView:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT project.project_id,project.quota_bytes,project.used_bytes,
                          project.reserved_bytes,project.lifecycle_policy_version
                   FROM dataset_store.projects project
                   JOIN product_auth.project_owners owner ON owner.project_id=project.project_id
                   WHERE project.project_id=%s AND project.state='active'
                     AND owner.owner_user_id=%s AND owner.state='active'
                     AND owner.authorization_version=%s""",
                (project_id, principal.user_id, authorization_version),
            ).fetchone()
        if row is None:
            raise ProductFacadeFailure("product_resource_unavailable")
        return self._row_to_project((row[0], authorization_version, *row[1:]))

    @staticmethod
    def _row_to_project(row) -> ProductProjectView:
        return ProductProjectView(
            project_id=row[0],
            authorization_version=row[1],
            quota_bytes=row[2],
            used_bytes=row[3],
            reserved_bytes=row[4],
            lifecycle_policy_version=row[5],
            capabilities=ProductCapabilities(),
        )

    @staticmethod
    def _clarification(
        request: DatasetAnalysisTaskRequestV1,
    ) -> ClarificationBundle | None:
        questions: list[ClarificationQuestion] = []
        intent = request.intent
        if intent.operation in {"aggregate", "compare", "trend"} and not intent.measures:
            questions.append(
                ClarificationQuestion(
                    code="measure_required",
                    field="intent.measures",
                    prompt_key="analysis.clarify.measure",
                )
            )
        if intent.operation == "compare" and not intent.dimensions and not intent.filters:
            questions.append(
                ClarificationQuestion(
                    code="comparison_basis_required",
                    field="intent.dimensions",
                    prompt_key="analysis.clarify.comparison_basis",
                )
            )
        if intent.operation == "trend" and intent.time_field is None:
            questions.append(
                ClarificationQuestion(
                    code="time_field_required",
                    field="intent.time_field",
                    prompt_key="analysis.clarify.time_field",
                )
            )
        if not questions:
            return None
        return ClarificationBundle(questions=tuple(questions))


__all__ = ["ALLOWED_OPERATIONS", "LocalProductFacadeService"]
