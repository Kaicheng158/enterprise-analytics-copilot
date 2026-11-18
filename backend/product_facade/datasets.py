"""Authorized, idempotent local Dataset upload/read composition for PV2-04b."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import hmac
import json
from typing import Callable
import unicodedata
from uuid import UUID, uuid5

import psycopg

from backend.auth.models import AuthPrincipal
from backend.authorization import ProjectAuthorizationService
from backend.datasets import FileSpec, ParseSettings, PersistenceFailure
from backend.datasets.persistence import PrivateDatasetPersistenceService
from backend.datasets.parser import ParseFailure
from backend.datasets.service import parse_file

from .models import (
    CreatePrivateProjectRequestV2,
    DatasetIssueLocationView,
    DatasetListResponse,
    DatasetPreviewCell,
    DatasetPreviewColumn,
    DatasetPreviewResponse,
    DatasetPreviewTable,
    DatasetQualityIssueView,
    DatasetQualitySummary,
    DatasetRevisionSummary,
    DatasetRevisionView,
    DatasetUploadMetadata,
    ProductCapabilitiesV2,
    ProductDatasetSettings,
    ProductFacadeFailure,
    ProductFacadeSettings,
    ProductProjectViewV2,
    ProductWorkspaceViewV2,
)


IDEMPOTENCY_KEY = r"^[A-Za-z0-9._~-]{16,128}$"
CONFIRMATION_CODES = frozenset(
    {
        "schema_confirmation_required",
        "numeric_ambiguous",
        "date_ambiguous",
        "boolean_ambiguous",
        "styled_cell_requires_review",
    }
)
BLOCKING_CODES = frozenset({"required_missing", "formula_like_text"})


class DatasetValidationRequired(Exception):
    """A content-free quality summary safe for the authorized HTTP boundary."""

    def __init__(self, quality: DatasetQualitySummary):
        self.quality = quality
        super().__init__("dataset_validation_required")


class AuthorizedDatasetProductService:
    """Compose live identity, owner authorization, parser and private storage.

    `trusted_local=True` is applied only inside this server-owned adapter after
    authorization.  No caller-provided value can reach that parameter.
    """

    def __init__(
        self,
        connection_factory: Callable,
        authorization: ProjectAuthorizationService,
        persistence: PrivateDatasetPersistenceService,
        *,
        facade_settings: ProductFacadeSettings,
        dataset_settings: ProductDatasetSettings,
        clock: Callable[[], datetime] | None = None,
    ):
        self._connections = connection_factory
        self._authorization = authorization
        self._persistence = persistence
        self._facade_settings = facade_settings
        self._settings = dataset_settings
        self._clock = clock or (lambda: datetime.now(UTC))

    def _enabled(self) -> None:
        if not self._facade_settings.enabled:
            raise ProductFacadeFailure("product_facade_disabled")
        if not self._settings.enabled:
            raise ProductFacadeFailure("product_dataset_upload_disabled")

    @contextmanager
    def _connection(self):
        try:
            with self._connections() as connection:
                if not connection.autocommit:
                    connection.autocommit = True
                yield connection
        except ProductFacadeFailure:
            raise
        except psycopg.Error:
            raise ProductFacadeFailure("product_storage_unavailable") from None

    @contextmanager
    def _idempotency_lock(self, principal: AuthPrincipal, operation: str, key_hash: str):
        lock_name = f"product-facade:{principal.user_id}:{operation}:{key_hash}"
        with self._connection() as connection:
            connection.execute(
                "SELECT pg_advisory_lock(hashtextextended(%s,0))", (lock_name,)
            )
            try:
                yield connection
            finally:
                try:
                    connection.execute(
                        "SELECT pg_advisory_unlock(hashtextextended(%s,0))", (lock_name,)
                    )
                except Exception:
                    pass

    def _key_hash(self, key: str) -> str:
        import re

        if not isinstance(key, str) or not re.fullmatch(IDEMPOTENCY_KEY, key):
            raise ProductFacadeFailure("product_idempotency_invalid")
        return hmac.new(
            self._settings.idempotency_secret.get_secret_value().encode(),
            key.encode(),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _request_hash(payload: dict) -> str:
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _stored_idempotency(connection, principal, operation, key_hash):
        return connection.execute(
            """SELECT request_sha256,project_id,asset_id,asset_revision_id,dataset_revision_id
               FROM product_facade.write_idempotency
               WHERE owner_user_id=%s AND operation=%s AND key_hash=%s""",
            (principal.user_id, operation, key_hash),
        ).fetchone()

    @staticmethod
    def _check_same_request(row, request_hash: str) -> None:
        if row is not None and row[0] != request_hash:
            raise ProductFacadeFailure("product_idempotency_conflict")

    def _capabilities(self) -> ProductCapabilitiesV2:
        return ProductCapabilitiesV2(dataset_upload=True, dataset_preview=True)

    def create_project(
        self,
        principal: AuthPrincipal,
        request: CreatePrivateProjectRequestV2,
        *,
        idempotency_key: str,
    ) -> ProductProjectViewV2:
        self._enabled()
        if type(principal) is not AuthPrincipal or type(request) is not CreatePrivateProjectRequestV2:
            raise ProductFacadeFailure("product_contract_invalid")
        key_hash = self._key_hash(idempotency_key)
        request_hash = self._request_hash(request.model_dump(mode="json"))
        project_id = uuid5(principal.user_id, "product-project:" + key_hash)
        with self._idempotency_lock(principal, "project_create", key_hash) as connection:
            stored = self._stored_idempotency(connection, principal, "project_create", key_hash)
            self._check_same_request(stored, request_hash)
            if stored is None:
                owner = self._authorization.create_or_get_owned_project(
                    principal,
                    project_id=project_id,
                    quota_bytes=self._facade_settings.default_project_quota_bytes,
                    lifecycle_policy_version=self._facade_settings.lifecycle_policy_version,
                )
                with connection.transaction():
                    connection.execute(
                        """INSERT INTO product_facade.project_profiles
                           (project_id,owner_user_id,display_name)
                           VALUES (%s,%s,%s) ON CONFLICT (project_id) DO NOTHING""",
                        (project_id, principal.user_id, request.name),
                    )
                    profile = connection.execute(
                        """SELECT owner_user_id,display_name FROM product_facade.project_profiles
                           WHERE project_id=%s""",
                        (project_id,),
                    ).fetchone()
                    if profile != (principal.user_id, request.name):
                        raise ProductFacadeFailure("product_idempotency_conflict")
                    connection.execute(
                        """INSERT INTO product_facade.write_idempotency
                           (owner_user_id,operation,key_hash,request_sha256,project_id)
                           VALUES (%s,'project_create',%s,%s,%s)""",
                        (principal.user_id, key_hash, request_hash, owner.project_id),
                    )
            else:
                project_id = stored[1]
        return self.get_project(principal, project_id)

    def workspace(self, principal: AuthPrincipal) -> ProductWorkspaceViewV2:
        self._enabled()
        if type(principal) is not AuthPrincipal:
            raise ProductFacadeFailure("product_contract_invalid")
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT profile.project_id,profile.display_name,owner.authorization_version,
                          project.quota_bytes,project.used_bytes,project.reserved_bytes,
                          project.lifecycle_policy_version
                   FROM product_facade.project_profiles profile
                   JOIN product_auth.project_owners owner ON owner.project_id=profile.project_id
                   JOIN dataset_store.projects project ON project.project_id=profile.project_id
                   JOIN product_auth.sessions session
                     ON session.user_id=owner.owner_user_id AND session.session_id=%s
                   WHERE owner.owner_user_id=%s AND owner.state='active'
                     AND project.state='active' AND session.state='active'
                     AND session.expires_at>%s
                   ORDER BY profile.created_at,profile.project_id""",
                (principal.session_id, principal.user_id, self._clock()),
            ).fetchall()
        return ProductWorkspaceViewV2(
            projects=tuple(self._project_from_row(row) for row in rows),
            capabilities=self._capabilities(),
        )

    def get_project(self, principal: AuthPrincipal, project_id: UUID) -> ProductProjectViewV2:
        self._enabled()
        access = self._authorization.require_project(principal, project_id, action="project_read")
        with self._connection() as connection:
            row = connection.execute(
                """SELECT profile.project_id,profile.display_name,owner.authorization_version,
                          project.quota_bytes,project.used_bytes,project.reserved_bytes,
                          project.lifecycle_policy_version
                   FROM product_facade.project_profiles profile
                   JOIN product_auth.project_owners owner ON owner.project_id=profile.project_id
                   JOIN dataset_store.projects project ON project.project_id=profile.project_id
                   WHERE profile.project_id=%s AND owner.owner_user_id=%s
                     AND owner.state='active' AND owner.authorization_version=%s
                     AND project.state='active'""",
                (project_id, principal.user_id, access.authorization_version),
            ).fetchone()
        if row is None:
            raise ProductFacadeFailure("product_resource_unavailable")
        return self._project_from_row(row)

    def upload(
        self,
        principal: AuthPrincipal,
        project_id: UUID,
        metadata: DatasetUploadMetadata,
        source: bytes,
        *,
        idempotency_key: str,
    ) -> DatasetRevisionView:
        self._enabled()
        if type(principal) is not AuthPrincipal or type(metadata) is not DatasetUploadMetadata:
            raise ProductFacadeFailure("product_contract_invalid")
        if type(source) is not bytes or not source or len(source) > self._settings.max_file_bytes:
            raise ProductFacadeFailure("product_file_limit")
        key_hash = self._key_hash(idempotency_key)
        source_sha = hashlib.sha256(source).hexdigest()
        request_hash = self._request_hash(
            {
                "project_id": str(project_id),
                "metadata": metadata.model_dump(mode="json"),
                "source_sha256": source_sha,
            }
        )
        normalized_name = unicodedata.normalize("NFKC", metadata.dataset_name).casefold()
        asset_key = "dataset_" + hashlib.sha256(normalized_name.encode()).hexdigest()[:32]
        file_spec = FileSpec(
            project_id=str(project_id),
            dataset_id=asset_key,
            filename=metadata.filename,
            columns=metadata.columns,
        )
        parse_settings = ParseSettings(enabled=True, max_bytes=self._settings.max_file_bytes)

        with self._authorization.project_write_fence(principal, project_id):
            with self._idempotency_lock(principal, "dataset_upload", key_hash) as connection:
                stored = self._stored_idempotency(connection, principal, "dataset_upload", key_hash)
                self._check_same_request(stored, request_hash)
                if stored is not None:
                    return self.get_revision(principal, project_id, stored[4], reused=True)
                try:
                    parsed = parse_file(source, file_spec, parse_settings)
                except ParseFailure as error:
                    raise ProductFacadeFailure("product_file_invalid") from error
                quality = self._quality(parsed)
                if parsed.status != "validated":
                    raise DatasetValidationRequired(quality)
                try:
                    persisted = self._persistence.persist(
                        source,
                        file_spec,
                        parse_settings,
                        project_id=project_id,
                        asset_key=asset_key,
                        trusted_local=True,
                    )
                except PersistenceFailure as error:
                    raise self._persistence_failure(error) from None
                self._authorization.require_asset(
                    principal, project_id, persisted.asset.asset_id, action="asset_write"
                )
                with connection.transaction():
                    connection.execute(
                        """INSERT INTO product_facade.asset_profiles
                           (project_id,asset_id,display_name)
                           VALUES (%s,%s,%s) ON CONFLICT (project_id,asset_id) DO NOTHING""",
                        (
                            project_id,
                            persisted.asset.asset_id,
                            metadata.dataset_name,
                        ),
                    )
                    connection.execute(
                        """INSERT INTO product_facade.asset_revision_profiles
                           (project_id,asset_revision_id,original_filename)
                           VALUES (%s,%s,%s)
                           ON CONFLICT (project_id,asset_revision_id) DO NOTHING""",
                        (
                            project_id,
                            persisted.asset_revision.revision_id,
                            metadata.filename,
                        ),
                    )
                    connection.execute(
                        """INSERT INTO product_facade.write_idempotency
                           (owner_user_id,operation,key_hash,request_sha256,project_id,
                            asset_id,asset_revision_id,dataset_revision_id)
                           VALUES (%s,'dataset_upload',%s,%s,%s,%s,%s,%s)""",
                        (
                            principal.user_id,
                            key_hash,
                            request_hash,
                            project_id,
                            persisted.asset.asset_id,
                            persisted.asset_revision.revision_id,
                            persisted.dataset_revision.dataset_revision_id,
                        ),
                    )
                return self.get_revision(
                    principal,
                    project_id,
                    persisted.dataset_revision.dataset_revision_id,
                    reused=persisted.reused,
                    known_dataset=parsed,
                )

    def list_revisions(self, principal: AuthPrincipal, project_id: UUID) -> DatasetListResponse:
        self._enabled()
        initial = self._authorization.require_project(principal, project_id, action="project_read")
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT revision.project_id,revision.asset_id,revision.revision_id,
                          dataset.dataset_revision_id,profile.display_name,
                          revision_profile.original_filename,revision.revision_number,
                          revision.source_format,revision.source_bytes
                   FROM dataset_store.asset_revisions revision
                   JOIN dataset_store.dataset_revisions dataset
                     ON (dataset.project_id,dataset.asset_revision_id)=
                        (revision.project_id,revision.revision_id)
                   JOIN dataset_store.assets asset
                     ON (asset.project_id,asset.asset_id)=(revision.project_id,revision.asset_id)
                   JOIN product_facade.asset_profiles profile
                     ON (profile.project_id,profile.asset_id)=(asset.project_id,asset.asset_id)
                   JOIN product_facade.asset_revision_profiles revision_profile
                     ON (revision_profile.project_id,revision_profile.asset_revision_id)=
                        (revision.project_id,revision.revision_id)
                   WHERE revision.project_id=%s AND asset.state='active'
                     AND revision.state='ready' AND dataset.state='ready'
                   ORDER BY profile.created_at,revision.revision_number,revision.revision_id
                   LIMIT 100""",
                (project_id,),
            ).fetchall()
        final = self._authorization.require_project(principal, project_id, action="project_read")
        if final.authorization_version != initial.authorization_version:
            raise ProductFacadeFailure("product_authorization_changed")
        return DatasetListResponse(
            project_id=project_id,
            revisions=tuple(
                DatasetRevisionSummary(
                    project_id=row[0], asset_id=row[1], asset_revision_id=row[2],
                    dataset_revision_id=row[3], dataset_name=row[4], original_filename=row[5],
                    revision_number=row[6], source_format=row[7], source_bytes=row[8],
                )
                for row in rows
            ),
        )

    def get_revision(
        self,
        principal: AuthPrincipal,
        project_id: UUID,
        dataset_revision_id: UUID,
        *,
        reused: bool = False,
        known_dataset=None,
    ) -> DatasetRevisionView:
        self._enabled()
        row = self._revision_row(project_id, dataset_revision_id)
        initial = self._authorization.require_asset(
            principal, project_id, row[1], action="asset_read"
        )
        dataset = known_dataset or self._load(project_id, dataset_revision_id)
        final = self._authorization.require_asset(
            principal, project_id, row[1], action="asset_read"
        )
        if final.authorization_version != initial.authorization_version:
            raise ProductFacadeFailure("product_authorization_changed")
        return DatasetRevisionView(
            project_id=row[0], asset_id=row[1], asset_revision_id=row[2],
            dataset_revision_id=row[3], dataset_name=row[4], original_filename=row[5],
            revision_number=row[6], source_format=row[7], source_bytes=row[8],
            source_sha256=row[9], normalized_sha256=row[10],
            dataset_contract_version=row[11], parser_policy_version=row[12],
            reused=reused, quality=self._quality(dataset),
        )

    def preview(
        self, principal: AuthPrincipal, project_id: UUID, dataset_revision_id: UUID
    ) -> DatasetPreviewResponse:
        self._enabled()
        row = self._revision_row(project_id, dataset_revision_id)
        initial = self._authorization.require_asset(
            principal, project_id, row[1], action="asset_read"
        )
        dataset = self._load(project_id, dataset_revision_id)
        final = self._authorization.require_asset(
            principal, project_id, row[1], action="asset_read"
        )
        if final.authorization_version != initial.authorization_version:
            raise ProductFacadeFailure("product_authorization_changed")
        tables = []
        for table_index, table in enumerate(dataset.tables[:4], 1):
            columns = tuple(
                DatasetPreviewColumn(
                    name=column.name, kind=column.kind, required=column.required, unit=column.unit
                )
                for column in table.columns[: self._settings.max_preview_columns]
            )
            rows = tuple(
                tuple(
                    DatasetPreviewCell(
                        value=None if cell.value is None else "[redacted]",
                        kind=cell.kind,
                        row=cell.location.row,
                        column=cell.location.column,
                    )
                    for cell in data_row[: self._settings.max_preview_columns]
                )
                for data_row in table.rows[: self._settings.max_preview_rows]
            )
            tables.append(
                DatasetPreviewTable(
                    table_index=table_index,
                    sheet=table.sheet,
                    columns=columns,
                    rows=rows,
                    total_rows=len(table.rows),
                    total_columns=len(table.columns),
                )
            )
        return DatasetPreviewResponse(
            project_id=project_id,
            asset_id=row[1],
            asset_revision_id=row[2],
            dataset_revision_id=dataset_revision_id,
            tables=tuple(tables),
            quality=self._quality(dataset),
        )

    def _load(self, project_id: UUID, dataset_revision_id: UUID):
        try:
            return self._persistence.load_dataset(
                project_id=project_id,
                dataset_revision_id=dataset_revision_id,
                trusted_local=True,
            )
        except PersistenceFailure as error:
            raise self._persistence_failure(error) from None

    def _revision_row(self, project_id: UUID, dataset_revision_id: UUID):
        with self._connection() as connection:
            row = connection.execute(
                """SELECT revision.project_id,revision.asset_id,revision.revision_id,
                          dataset.dataset_revision_id,profile.display_name,
                          revision_profile.original_filename,revision.revision_number,
                          revision.source_format,revision.source_bytes,
                          revision.source_sha256,revision.normalized_sha256,
                          dataset.dataset_contract_version,dataset.parser_policy_version
                   FROM dataset_store.asset_revisions revision
                   JOIN dataset_store.dataset_revisions dataset
                     ON (dataset.project_id,dataset.asset_revision_id)=
                        (revision.project_id,revision.revision_id)
                   JOIN dataset_store.assets asset
                     ON (asset.project_id,asset.asset_id)=(revision.project_id,revision.asset_id)
                   JOIN product_facade.asset_profiles profile
                     ON (profile.project_id,profile.asset_id)=(asset.project_id,asset.asset_id)
                   JOIN product_facade.asset_revision_profiles revision_profile
                     ON (revision_profile.project_id,revision_profile.asset_revision_id)=
                        (revision.project_id,revision.revision_id)
                   WHERE revision.project_id=%s AND dataset.dataset_revision_id=%s
                     AND asset.state='active' AND revision.state='ready' AND dataset.state='ready'""",
                (project_id, dataset_revision_id),
            ).fetchone()
        if row is None:
            raise ProductFacadeFailure("product_resource_unavailable")
        return row

    def _project_from_row(self, row) -> ProductProjectViewV2:
        return ProductProjectViewV2(
            project_id=row[0], name=row[1], authorization_version=row[2],
            quota_bytes=row[3], used_bytes=row[4], reserved_bytes=row[5],
            lifecycle_policy_version=row[6], capabilities=self._capabilities(),
        )

    @staticmethod
    def _quality(dataset) -> DatasetQualitySummary:
        groups: dict[str, list] = {
            "blocking_error": [], "confirmation_required": [], "warning": []
        }
        for issue in dataset.issues:
            if issue.severity == "warning":
                category = "warning"
            elif issue.code in CONFIRMATION_CODES:
                category = "confirmation_required"
            elif issue.code in BLOCKING_CODES:
                category = "blocking_error"
            else:
                category = "blocking_error"
            groups[category].append(
                DatasetQualityIssueView(
                    code=issue.code,
                    category=category,
                    location=DatasetIssueLocationView(
                        table_index=issue.location.table,
                        sheet=issue.location.sheet,
                        row=issue.location.row,
                        column=issue.location.column,
                        line_start=issue.location.line_start,
                        line_end=issue.location.line_end,
                    ),
                )
            )
        return DatasetQualitySummary(
            blocking_errors=tuple(groups["blocking_error"]),
            confirmations=tuple(groups["confirmation_required"]),
            warnings=tuple(groups["warning"]),
        )

    @staticmethod
    def _persistence_failure(error: PersistenceFailure) -> ProductFacadeFailure:
        if error.code == "quota_exceeded":
            return ProductFacadeFailure("product_quota_exceeded")
        if error.code in {
            "dataset_revision_unavailable", "project_unavailable", "asset_unavailable"
        }:
            return ProductFacadeFailure("product_resource_unavailable")
        if error.code in {
            "stored_blob_integrity_mismatch", "stored_dataset_contract_invalid",
            "stored_dataset_integrity_mismatch", "reconciliation_required",
            "finalization_uncertain", "storage_cleanup_uncertain",
        }:
            return ProductFacadeFailure("product_reconciliation_required")
        return ProductFacadeFailure("product_storage_unavailable")


__all__ = ["AuthorizedDatasetProductService", "DatasetValidationRequired"]
