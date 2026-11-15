"""Project-scoped metadata + private blob coordination for PV2-02a."""
from contextlib import contextmanager
import hashlib
import json
import re
from typing import Callable
import uuid

from .models import FileSpec, ParseSettings
from .persistence_models import (
    Asset,
    AssetRevision,
    DatasetPersistenceSettings,
    DatasetRevision,
    PersistedDataset,
    PersistenceFailure,
    Project,
    ReconciliationResult,
)
from .private_storage import PrivateLocalBlobStore
from .service import parse_file


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _dataset_bytes(dataset) -> bytes:
    payload = dataset.model_dump(mode="json")
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


class PrivateDatasetPersistenceService:
    """Trusted server-side service. project_id is a locator, never authorization."""

    def __init__(
        self,
        connection_factory: Callable,
        blob_store: PrivateLocalBlobStore,
        *,
        settings: DatasetPersistenceSettings | None = None,
        after_reserve: Callable[[], None] | None = None,
        before_finalize: Callable[[], None] | None = None,
    ):
        self._connections = connection_factory
        self._blobs = blob_store
        self._settings = settings or DatasetPersistenceSettings()
        self._after_reserve = after_reserve
        self._before_finalize = before_finalize

    def _authorize(self, trusted_local: bool) -> None:
        if not self._settings.enabled:
            raise PersistenceFailure("persistence_disabled")
        if trusted_local is not True:
            raise PersistenceFailure("trusted_local_required")

    @contextmanager
    def _connection(self):
        try:
            with self._connections() as connection:
                if not connection.autocommit:
                    connection.autocommit = True
                yield connection
        except PersistenceFailure:
            raise
        except Exception:
            raise PersistenceFailure("database_error") from None

    def create_project(
        self,
        *,
        project_id: uuid.UUID,
        scope_key: str,
        quota_bytes: int,
        lifecycle_policy_version: str,
        trusted_local: bool = False,
    ) -> Project:
        self._authorize(trusted_local)
        if type(project_id) is not uuid.UUID or not 1 <= len(scope_key) <= 160:
            raise PersistenceFailure("invalid_project_contract")
        if type(quota_bytes) is not int or quota_bytes <= 0 or not 1 <= len(lifecycle_policy_version) <= 80:
            raise PersistenceFailure("invalid_project_contract")
        with self._connection() as connection, connection.transaction():
            connection.execute(
                """INSERT INTO dataset_store.projects
                   (project_id,scope_key,quota_bytes,lifecycle_policy_version)
                   VALUES (%s,%s,%s,%s) ON CONFLICT (project_id) DO NOTHING""",
                (project_id, scope_key, quota_bytes, lifecycle_policy_version),
            )
            row = connection.execute(
                """SELECT project_id,scope_key,state,quota_bytes,used_bytes,reserved_bytes,lifecycle_policy_version
                   FROM dataset_store.projects WHERE project_id=%s""",
                (project_id,),
            ).fetchone()
            project = self._project(row)
            if (
                project.scope_key != scope_key
                or project.quota_bytes != quota_bytes
                or project.lifecycle_policy_version != lifecycle_policy_version
            ):
                raise PersistenceFailure("project_contract_conflict")
            return project

    def persist(
        self,
        source: bytes,
        spec: FileSpec,
        parse_settings: ParseSettings,
        *,
        project_id: uuid.UUID,
        asset_key: str,
        trusted_local: bool = False,
    ) -> PersistedDataset:
        self._authorize(trusted_local)
        if type(project_id) is not uuid.UUID or str(project_id) != spec.project_id:
            raise PersistenceFailure("project_scope_mismatch")
        if not isinstance(asset_key, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", asset_key):
            raise PersistenceFailure("invalid_asset_key")
        dataset = parse_file(source, spec, parse_settings)
        if dataset.status != "validated":
            raise PersistenceFailure("dataset_requires_review")
        normalized = _dataset_bytes(dataset)
        normalized_blob_sha = _sha256(normalized)
        reservation = len(source) + len(normalized)
        request_sha = _sha256(
            (dataset.source_sha256 + dataset.parse_contract_sha256 + dataset.normalized_sha256).encode()
        )
        asset_id = uuid.uuid5(project_id, "asset:" + asset_key)
        lock_keys = sorted((
            f"{project_id}:asset:{asset_key}",
            f"{project_id}:content:{dataset.source_sha256}:{dataset.parse_contract_sha256}",
        ))

        with self._connection() as connection:
            for lock_key in lock_keys:
                connection.execute("SELECT pg_advisory_lock(hashtextextended(%s,0))", (lock_key,))
            try:
                existing = self._reserve(
                    connection,
                    project_id,
                    asset_id,
                    asset_key,
                    dataset,
                    normalized_blob_sha,
                    len(source),
                    len(normalized),
                    reservation,
                    request_sha,
                )
                if existing["reused"]:
                    if not self._verify_revision_blobs(connection, project_id, existing["revision_id"]):
                        raise PersistenceFailure("stored_blob_integrity_mismatch")
                    return self._result(connection, project_id, existing["asset_id"], existing["revision_id"], True)
                operation_id = existing["operation_id"]
                raw_ref = existing["raw_ref"]
                dataset_ref = existing["dataset_ref"]
                if self._after_reserve:
                    self._after_reserve()
                try:
                    raw_size, raw_hash = self._blobs.put(raw_ref, source)
                    if raw_size != len(source) or raw_hash != dataset.source_sha256:
                        raise PersistenceFailure("storage_integrity_failed")
                    dataset_size, dataset_hash = self._blobs.put(dataset_ref, normalized)
                    if dataset_size != len(normalized) or dataset_hash != normalized_blob_sha:
                        raise PersistenceFailure("storage_integrity_failed")
                except PersistenceFailure as exc:
                    try:
                        self._blobs.remove(raw_ref)
                        self._blobs.remove(dataset_ref)
                    except PersistenceFailure:
                        self._mark_reconciling(connection, operation_id)
                        raise PersistenceFailure("storage_cleanup_uncertain") from None
                    self._fail_operation(connection, operation_id, "storage_write_failed")
                    raise PersistenceFailure("storage_write_failed") from exc
                try:
                    if self._before_finalize:
                        self._before_finalize()
                    self._finalize(connection, operation_id)
                except Exception:
                    self._mark_reconciling(connection, operation_id)
                    raise PersistenceFailure("finalization_uncertain") from None
                return self._result(connection, project_id, existing["asset_id"], existing["revision_id"], False)
            finally:
                for lock_key in reversed(lock_keys):
                    try:
                        connection.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (lock_key,))
                    except Exception:
                        pass

    def reconcile(
        self, operation_id: uuid.UUID, *, trusted_local: bool = False
    ) -> ReconciliationResult:
        self._authorize(trusted_local)
        if type(operation_id) is not uuid.UUID:
            raise PersistenceFailure("invalid_operation_id")
        with self._connection() as connection:
            with connection.transaction():
                row = connection.execute(
                    """SELECT o.state,o.reserved_bytes,a.storage_ref,a.source_bytes,a.source_sha256,
                              d.storage_ref,d.dataset_bytes,d.blob_sha256
                       FROM dataset_store.storage_operations o
                       JOIN dataset_store.asset_revisions a
                         ON (a.project_id,a.revision_id)=(o.project_id,o.asset_revision_id)
                       JOIN dataset_store.dataset_revisions d
                         ON (d.project_id,d.asset_revision_id)=(a.project_id,a.revision_id)
                       WHERE o.operation_id=%s FOR UPDATE""",
                    (operation_id,),
                ).fetchone()
                if not row:
                    raise PersistenceFailure("operation_not_found")
                if row[0] == "ready":
                    return ReconciliationResult(
                        operation_id=operation_id, state="ready", action="published_verified_blobs"
                    )
                if row[0] not in {"writing", "reconciling", "reserved"}:
                    raise PersistenceFailure("operation_not_reconcilable")
                valid = self._blobs.verify(row[2], row[3], row[4]) and self._blobs.verify(
                    row[5], row[6], row[7]
                )
            if valid:
                self._finalize(connection, operation_id)
                return ReconciliationResult(
                    operation_id=operation_id, state="ready", action="published_verified_blobs"
                )
            self._blobs.remove(row[2])
            self._blobs.remove(row[5])
            self._fail_operation(connection, operation_id, "blob_missing_or_mismatched")
            return ReconciliationResult(
                operation_id=operation_id,
                state="failed",
                action="failed_missing_or_mismatched_blob",
            )

    def _reserve(self, connection, project_id, asset_id, asset_key, dataset, blob_hash,
                 source_size, dataset_size, reservation, request_sha):
        raw_ref = self._blobs.new_reference("raw")
        dataset_ref = self._blobs.new_reference("dataset")
        operation_id = uuid.uuid4()
        revision_id = uuid.uuid5(asset_id, dataset.source_sha256 + ":" + dataset.parse_contract_sha256)
        dataset_revision_id = uuid.uuid5(revision_id, dataset.normalized_sha256)
        with connection.transaction():
            project = connection.execute(
                "SELECT state,lifecycle_policy_version FROM dataset_store.projects WHERE project_id=%s FOR UPDATE",
                (project_id,),
            ).fetchone()
            if not project or project[0] != "active":
                raise PersistenceFailure("project_unavailable")
            prior = connection.execute(
                """SELECT asset_id,revision_id,state,normalized_sha256,parser_policy_version FROM dataset_store.asset_revisions
                   WHERE project_id=%s AND source_sha256=%s AND parse_contract_sha256=%s""",
                (project_id, dataset.source_sha256, dataset.parse_contract_sha256),
            ).fetchone()
            if prior:
                if prior[3] != dataset.normalized_sha256 or prior[4] != dataset.policy_version:
                    raise PersistenceFailure("stored_revision_integrity_mismatch")
                if prior[2] == "ready":
                    return {"reused": True, "asset_id": prior[0], "revision_id": prior[1]}
                if prior[2] == "failed":
                    updated = connection.execute(
                        """UPDATE dataset_store.projects SET reserved_bytes=reserved_bytes+%s,updated_at=now()
                           WHERE project_id=%s AND used_bytes+reserved_bytes+%s<=quota_bytes RETURNING project_id""",
                        (reservation, project_id, reservation),
                    ).fetchone()
                    if not updated:
                        raise PersistenceFailure("quota_exceeded")
                    retry = connection.execute(
                        """SELECT o.operation_id,a.storage_ref,d.storage_ref FROM dataset_store.storage_operations o
                           JOIN dataset_store.asset_revisions a ON (a.project_id,a.revision_id)=(o.project_id,o.asset_revision_id)
                           JOIN dataset_store.dataset_revisions d ON (d.project_id,d.asset_revision_id)=(a.project_id,a.revision_id)
                           WHERE o.project_id=%s AND o.asset_revision_id=%s AND o.state='failed' FOR UPDATE""",
                        (project_id, prior[1]),
                    ).fetchone()
                    if not retry:
                        raise PersistenceFailure("stored_revision_inconsistent")
                    connection.execute("UPDATE dataset_store.asset_revisions SET state='pending',ready_at=NULL WHERE project_id=%s AND revision_id=%s", (project_id, prior[1]))
                    connection.execute("UPDATE dataset_store.dataset_revisions SET state='pending',ready_at=NULL WHERE project_id=%s AND asset_revision_id=%s", (project_id, prior[1]))
                    connection.execute("""UPDATE dataset_store.storage_operations SET state='writing',reserved_bytes=%s,
                        actual_bytes=NULL,error_code=NULL,updated_at=now() WHERE operation_id=%s""", (reservation,retry[0]))
                    return {"reused": False,"operation_id":retry[0],"asset_id":prior[0],"revision_id":prior[1],"raw_ref":retry[1],"dataset_ref":retry[2]}
                raise PersistenceFailure("reconciliation_required")
            connection.execute(
                """INSERT INTO dataset_store.assets(project_id,asset_id,asset_key)
                   VALUES (%s,%s,%s) ON CONFLICT (project_id,asset_key) DO NOTHING""",
                (project_id, asset_id, asset_key),
            )
            asset = connection.execute(
                "SELECT asset_id,state FROM dataset_store.assets WHERE project_id=%s AND asset_key=%s",
                (project_id, asset_key),
            ).fetchone()
            if not asset or asset[0] != asset_id or asset[1] != "active":
                raise PersistenceFailure("asset_unavailable")
            updated = connection.execute(
                """UPDATE dataset_store.projects SET reserved_bytes=reserved_bytes+%s,updated_at=now()
                   WHERE project_id=%s AND used_bytes+reserved_bytes+%s<=quota_bytes RETURNING project_id""",
                (reservation, project_id, reservation),
            ).fetchone()
            if not updated:
                raise PersistenceFailure("quota_exceeded")
            revision_number = connection.execute(
                "SELECT COALESCE(max(revision_number),0)+1 FROM dataset_store.asset_revisions WHERE project_id=%s AND asset_id=%s",
                (project_id, asset_id),
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO dataset_store.asset_revisions
                   (project_id,asset_id,revision_id,revision_number,source_sha256,normalized_sha256,
                    parse_contract_sha256,source_format,source_bytes,storage_ref,state,parser_policy_version,lifecycle_policy_version)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s)""",
                (project_id, asset_id, revision_id, revision_number, dataset.source_sha256,
                 dataset.normalized_sha256, dataset.parse_contract_sha256, dataset.source_format,
                 source_size, raw_ref, dataset.policy_version, project[1]),
            )
            connection.execute(
                """INSERT INTO dataset_store.dataset_revisions
                   (project_id,dataset_revision_id,asset_revision_id,normalized_sha256,blob_sha256,
                    dataset_contract_version,parser_policy_version,dataset_bytes,storage_ref,state)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending')""",
                (project_id, dataset_revision_id, revision_id, dataset.normalized_sha256, blob_hash,
                 dataset.contract_version, dataset.policy_version, dataset_size, dataset_ref),
            )
            connection.execute(
                """INSERT INTO dataset_store.storage_operations
                   (operation_id,project_id,asset_id,asset_revision_id,request_sha256,state,reserved_bytes)
                   VALUES (%s,%s,%s,%s,%s,'reserved',%s)""",
                (operation_id, project_id, asset_id, revision_id, request_sha, reservation),
            )
            connection.execute(
                "UPDATE dataset_store.storage_operations SET state='writing',updated_at=now() WHERE operation_id=%s",
                (operation_id,),
            )
        return {"reused": False, "operation_id": operation_id, "asset_id": asset_id, "revision_id": revision_id,
                "raw_ref": raw_ref, "dataset_ref": dataset_ref}

    def _finalize(self, connection, operation_id):
        with connection.transaction():
            row = connection.execute(
                """SELECT project_id,asset_revision_id,reserved_bytes,state FROM dataset_store.storage_operations
                   WHERE operation_id=%s FOR UPDATE""",
                (operation_id,),
            ).fetchone()
            if not row:
                raise PersistenceFailure("operation_not_found")
            if row[3] == "ready":
                return
            if row[3] not in {"writing", "reconciling", "reserved"}:
                raise PersistenceFailure("operation_not_finalizable")
            project_id, revision_id, reserved, _ = row
            connection.execute(
                """UPDATE dataset_store.projects SET reserved_bytes=reserved_bytes-%s,
                   used_bytes=used_bytes+%s,updated_at=now() WHERE project_id=%s""",
                (reserved, reserved, project_id),
            )
            connection.execute(
                "UPDATE dataset_store.asset_revisions SET state='ready',ready_at=now() WHERE project_id=%s AND revision_id=%s",
                (project_id, revision_id),
            )
            connection.execute(
                "UPDATE dataset_store.dataset_revisions SET state='ready',ready_at=now() WHERE project_id=%s AND asset_revision_id=%s",
                (project_id, revision_id),
            )
            connection.execute(
                """UPDATE dataset_store.storage_operations SET state='ready',actual_bytes=%s,
                   reserved_bytes=0,updated_at=now() WHERE operation_id=%s""",
                (reserved, operation_id),
            )

    def _mark_reconciling(self, connection, operation_id):
        try:
            with connection.transaction():
                row = connection.execute(
                    "SELECT project_id,asset_revision_id FROM dataset_store.storage_operations WHERE operation_id=%s FOR UPDATE",
                    (operation_id,),
                ).fetchone()
                if not row:
                    return
                connection.execute("UPDATE dataset_store.storage_operations SET state='reconciling',updated_at=now() WHERE operation_id=%s AND state IN ('reserved','writing')", (operation_id,))
                connection.execute("UPDATE dataset_store.asset_revisions SET state='reconciling' WHERE project_id=%s AND revision_id=%s AND state='pending'", row)
                connection.execute("UPDATE dataset_store.dataset_revisions SET state='reconciling' WHERE project_id=%s AND asset_revision_id=%s AND state='pending'", row)
        except Exception:
            return

    def _fail_operation(self, connection, operation_id, error_code):
        with connection.transaction():
            row = connection.execute(
                "SELECT project_id,asset_revision_id,reserved_bytes FROM dataset_store.storage_operations WHERE operation_id=%s FOR UPDATE",
                (operation_id,),
            ).fetchone()
            if not row:
                raise PersistenceFailure("operation_not_found")
            project_id, revision_id, reserved = row
            connection.execute("UPDATE dataset_store.projects SET reserved_bytes=reserved_bytes-%s,updated_at=now() WHERE project_id=%s", (reserved, project_id))
            connection.execute("UPDATE dataset_store.asset_revisions SET state='failed' WHERE project_id=%s AND revision_id=%s", (project_id, revision_id))
            connection.execute("UPDATE dataset_store.dataset_revisions SET state='failed' WHERE project_id=%s AND asset_revision_id=%s", (project_id, revision_id))
            connection.execute("UPDATE dataset_store.storage_operations SET state='failed',reserved_bytes=0,error_code=%s,updated_at=now() WHERE operation_id=%s", (error_code, operation_id))

    def _result(self, connection, project_id, asset_id, revision_id, reused):
        project = self._project(connection.execute(
            "SELECT project_id,scope_key,state,quota_bytes,used_bytes,reserved_bytes,lifecycle_policy_version FROM dataset_store.projects WHERE project_id=%s",
            (project_id,),
        ).fetchone())
        asset_row = connection.execute("SELECT project_id,asset_id,asset_key,state FROM dataset_store.assets WHERE project_id=%s AND asset_id=%s", (project_id, asset_id)).fetchone()
        revision_row = connection.execute("""SELECT project_id,asset_id,revision_id,revision_number,source_sha256,normalized_sha256,
            parse_contract_sha256,source_format,source_bytes,state,parser_policy_version,lifecycle_policy_version,retention_until
            FROM dataset_store.asset_revisions WHERE project_id=%s AND revision_id=%s AND state='ready'""", (project_id, revision_id)).fetchone()
        dataset_row = connection.execute("""SELECT project_id,dataset_revision_id,asset_revision_id,normalized_sha256,
            dataset_contract_version,parser_policy_version,dataset_bytes,state FROM dataset_store.dataset_revisions
            WHERE project_id=%s AND asset_revision_id=%s AND state='ready'""", (project_id, revision_id)).fetchone()
        operation = connection.execute("SELECT operation_id FROM dataset_store.storage_operations WHERE project_id=%s AND asset_revision_id=%s", (project_id, revision_id)).fetchone()
        if not all((asset_row, revision_row, dataset_row, operation)):
            raise PersistenceFailure("stored_revision_inconsistent")
        return PersistedDataset(project=project, asset=Asset(project_id=asset_row[0],asset_id=asset_row[1],asset_key=asset_row[2],state=asset_row[3]),
            asset_revision=AssetRevision(project_id=revision_row[0],asset_id=revision_row[1],revision_id=revision_row[2],revision_number=revision_row[3],source_sha256=revision_row[4],normalized_sha256=revision_row[5],parse_contract_sha256=revision_row[6],source_format=revision_row[7],source_bytes=revision_row[8],state=revision_row[9],parser_policy_version=revision_row[10],lifecycle_policy_version=revision_row[11],retention_until=revision_row[12]),
            dataset_revision=DatasetRevision(project_id=dataset_row[0],dataset_revision_id=dataset_row[1],asset_revision_id=dataset_row[2],normalized_sha256=dataset_row[3],dataset_contract_version=dataset_row[4],parser_policy_version=dataset_row[5],dataset_bytes=dataset_row[6],state=dataset_row[7]), reused=reused, operation_id=operation[0])

    def _verify_revision_blobs(self, connection, project_id, revision_id):
        row = connection.execute(
            """SELECT a.storage_ref,a.source_bytes,a.source_sha256,d.storage_ref,d.dataset_bytes,d.blob_sha256
               FROM dataset_store.asset_revisions a JOIN dataset_store.dataset_revisions d
                 ON (d.project_id,d.asset_revision_id)=(a.project_id,a.revision_id)
               WHERE a.project_id=%s AND a.revision_id=%s AND a.state='ready' AND d.state='ready'""",
            (project_id, revision_id),
        ).fetchone()
        return bool(row) and self._blobs.verify(row[0], row[1], row[2]) and self._blobs.verify(
            row[3], row[4], row[5]
        )

    @staticmethod
    def _project(row):
        if not row:
            raise PersistenceFailure("project_not_found")
        return Project(project_id=row[0],scope_key=row[1],state=row[2],quota_bytes=row[3],used_bytes=row[4],reserved_bytes=row[5],lifecycle_policy_version=row[6])
