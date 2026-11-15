"""Bounded, trusted-local lifecycle maintenance for private Dataset revisions."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Callable
import uuid

from .lifecycle_models import (
    DatasetLifecycleSettings,
    LifecycleOperationResult,
    MaintenanceResult,
)
from .persistence import MAINTENANCE_LOCK, PrivateDatasetPersistenceService
from .persistence_models import DatasetPersistenceSettings, PersistenceFailure
from .private_storage import PrivateLocalBlobStore


class PrivateDatasetLifecycleService:
    """Lifecycle boundary. Identifiers locate records; trusted_local is still mandatory."""

    def __init__(
        self,
        connection_factory: Callable,
        blob_store: PrivateLocalBlobStore,
        *,
        settings: DatasetLifecycleSettings | None = None,
        after_first_blob_delete: Callable[[], None] | None = None,
    ):
        self._connections = connection_factory
        self._blobs = blob_store
        self._settings = settings or DatasetLifecycleSettings()
        self._after_first_blob_delete = after_first_blob_delete
        self._persistence = PrivateDatasetPersistenceService(
            connection_factory,
            blob_store,
            settings=DatasetPersistenceSettings(enabled=True),
        )

    def _authorize(self, trusted_local: bool) -> None:
        if not self._settings.enabled:
            raise PersistenceFailure("lifecycle_disabled")
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

    @contextmanager
    def _maintenance_lock(self, connection, *, exclusive: bool, wait: bool = False):
        suffix = "" if exclusive else "_shared"
        function = f"pg_advisory_lock{suffix}" if wait else f"pg_try_advisory_lock{suffix}"
        acquired = connection.execute(
            f"SELECT {function}(hashtextextended(%s,0))", (MAINTENANCE_LOCK,)
        ).fetchone()[0]
        if acquired is False:
            raise PersistenceFailure("maintenance_busy")
        try:
            yield
        finally:
            unlock = "pg_advisory_unlock" if exclusive else "pg_advisory_unlock_shared"
            try:
                connection.execute(
                    f"SELECT {unlock}(hashtextextended(%s,0))", (MAINTENANCE_LOCK,)
                )
            except Exception:
                pass

    @contextmanager
    def _project_lock(self, connection, project_id: uuid.UUID):
        key = f"dataset_store:project:{project_id}"
        connection.execute("SELECT pg_advisory_lock(hashtextextended(%s,0))", (key,))
        try:
            yield
        finally:
            try:
                connection.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (key,))
            except Exception:
                pass

    def set_retention(
        self,
        project_id: uuid.UUID,
        revision_id: uuid.UUID,
        retention_until: datetime,
        *,
        trusted_local: bool = False,
    ) -> None:
        self._authorize(trusted_local)
        self._validate_ids(project_id, revision_id)
        if not isinstance(retention_until, datetime) or retention_until.tzinfo is None:
            raise PersistenceFailure("invalid_retention_time")
        with self._connection() as connection, self._maintenance_lock(
            connection, exclusive=False, wait=True
        ), self._project_lock(connection, project_id), connection.transaction():
            row = connection.execute(
                """UPDATE dataset_store.asset_revisions SET retention_until=%s
                   WHERE project_id=%s AND revision_id=%s AND state='ready'
                   RETURNING revision_id""",
                (retention_until, project_id, revision_id),
            ).fetchone()
            if not row:
                raise PersistenceFailure("revision_not_ready")
            self._audit(
                connection,
                event_type="retention_set",
                result="completed",
                project_id=project_id,
                revision_id=revision_id,
            )

    def require_readable(
        self,
        project_id: uuid.UUID,
        revision_id: uuid.UUID,
        *,
        trusted_local: bool = False,
    ) -> None:
        self._authorize(trusted_local)
        self._validate_ids(project_id, revision_id)
        with self._connection() as connection:
            row = connection.execute(
                """SELECT 1 FROM dataset_store.asset_revisions a
                   JOIN dataset_store.dataset_revisions d
                     ON (d.project_id,d.asset_revision_id)=(a.project_id,a.revision_id)
                   JOIN dataset_store.assets s
                     ON (s.project_id,s.asset_id)=(a.project_id,a.asset_id)
                   WHERE a.project_id=%s AND a.revision_id=%s
                     AND a.state='ready' AND d.state='ready' AND s.state='active'""",
                (project_id, revision_id),
            ).fetchone()
        if not row:
            raise PersistenceFailure("revision_unavailable")

    def mark_for_deletion(
        self,
        project_id: uuid.UUID,
        revision_id: uuid.UUID,
        *,
        trusted_local: bool = False,
    ) -> LifecycleOperationResult:
        self._authorize(trusted_local)
        self._validate_ids(project_id, revision_id)
        with self._connection() as connection, self._maintenance_lock(
            connection, exclusive=False, wait=True
        ), self._project_lock(connection, project_id), connection.transaction():
            existing = connection.execute(
                """SELECT operation_id,state,logical_bytes,quota_released_bytes
                   FROM dataset_store.lifecycle_operations
                   WHERE project_id=%s AND asset_revision_id=%s AND action='delete_revision'
                   FOR UPDATE""",
                (project_id, revision_id),
            ).fetchone()
            if existing:
                return self._operation_result(existing, project_id, revision_id)
            revision = connection.execute(
                """SELECT a.asset_id,a.state,a.source_bytes,d.state,d.dataset_bytes
                   FROM dataset_store.asset_revisions a
                   JOIN dataset_store.dataset_revisions d
                     ON (d.project_id,d.asset_revision_id)=(a.project_id,a.revision_id)
                   WHERE a.project_id=%s AND a.revision_id=%s FOR UPDATE OF a,d""",
                (project_id, revision_id),
            ).fetchone()
            if not revision:
                raise PersistenceFailure("revision_not_found")
            if revision[1] not in {"ready", "expired"} or revision[3] not in {"ready", "expired"}:
                raise PersistenceFailure("revision_not_deletable")
            operation_id = uuid.uuid4()
            logical_bytes = revision[2] + revision[4]
            connection.execute(
                """UPDATE dataset_store.asset_revisions
                   SET state='deleting',ready_at=NULL,deletion_marked_at=now()
                   WHERE project_id=%s AND revision_id=%s""",
                (project_id, revision_id),
            )
            connection.execute(
                """UPDATE dataset_store.dataset_revisions SET state='deleting',ready_at=NULL
                   WHERE project_id=%s AND asset_revision_id=%s""",
                (project_id, revision_id),
            )
            connection.execute(
                """UPDATE dataset_store.assets SET state=
                     CASE WHEN EXISTS (
                       SELECT 1 FROM dataset_store.asset_revisions r
                       WHERE r.project_id=%s AND r.asset_id=%s AND r.revision_id<>%s AND r.state='ready'
                     ) THEN 'active' ELSE 'deleting' END
                   WHERE project_id=%s AND asset_id=%s""",
                (project_id, revision[0], revision_id, project_id, revision[0]),
            )
            connection.execute(
                """INSERT INTO dataset_store.lifecycle_operations
                   (operation_id,project_id,asset_id,asset_revision_id,action,state,logical_bytes)
                   VALUES (%s,%s,%s,%s,'delete_revision','marked',%s)""",
                (operation_id, project_id, revision[0], revision_id, logical_bytes),
            )
            self._audit(
                connection,
                event_type="deletion_marked",
                result="started",
                project_id=project_id,
                revision_id=revision_id,
                operation_id=operation_id,
                logical_bytes=logical_bytes,
            )
            return LifecycleOperationResult(
                operation_id=operation_id,
                project_id=project_id,
                revision_id=revision_id,
                state="marked",
                logical_bytes=logical_bytes,
                quota_released_bytes=0,
            )

    def resume_deletion(
        self,
        operation_id: uuid.UUID,
        *,
        trusted_local: bool = False,
        worker_id: uuid.UUID | None = None,
    ) -> LifecycleOperationResult:
        self._authorize(trusted_local)
        if type(operation_id) is not uuid.UUID:
            raise PersistenceFailure("invalid_operation_id")
        owner = worker_id or uuid.uuid4()
        if type(owner) is not uuid.UUID:
            raise PersistenceFailure("invalid_worker_id")
        with self._connection() as initial:
            identity = initial.execute(
                """SELECT project_id,asset_revision_id FROM dataset_store.lifecycle_operations
                   WHERE operation_id=%s""",
                (operation_id,),
            ).fetchone()
        if not identity:
            raise PersistenceFailure("lifecycle_operation_not_found")
        project_id, revision_id = identity
        with self._connection() as connection, self._maintenance_lock(
            connection, exclusive=False, wait=True
        ), self._project_lock(connection, project_id):
            claimed = self._claim_deletion(connection, operation_id, owner)
            if claimed[0] == "completed":
                return self._operation_result(claimed[1:5], project_id, revision_id)
            fence, raw_ref, dataset_ref, logical_bytes = claimed[1:]
            try:
                self._blobs.remove(raw_ref)
                if self._after_first_blob_delete:
                    self._after_first_blob_delete()
                self._blobs.remove(dataset_ref)
            except PersistenceFailure:
                self._defer_deletion(connection, operation_id, owner, fence, "blob_delete_failed")
                raise PersistenceFailure("deletion_incomplete") from None
            return self._complete_deletion(
                connection,
                operation_id,
                project_id,
                revision_id,
                owner,
                fence,
                logical_bytes,
            )

    def run_maintenance(
        self,
        *,
        now: datetime,
        stale_before: datetime,
        limit: int,
        trusted_local: bool = False,
    ) -> MaintenanceResult:
        self._authorize(trusted_local)
        if (
            not isinstance(now, datetime)
            or not isinstance(stale_before, datetime)
            or now.tzinfo is None
            or stale_before.tzinfo is None
            or stale_before > now
            or type(limit) is not int
            or not 1 <= limit <= self._settings.max_items_per_run
        ):
            raise PersistenceFailure("invalid_maintenance_contract")
        run_id = uuid.uuid4()
        expired = storage_ready = storage_failed = deleted = deferred = 0
        orphan_count = staging_count = orphan_logical = physical = 0
        processed = 0

        with self._connection() as connection, self._maintenance_lock(
            connection, exclusive=True
        ):
            with connection.transaction():
                due = connection.execute(
                    """SELECT project_id,revision_id,asset_id
                       FROM dataset_store.asset_revisions
                       WHERE state='ready' AND retention_until IS NOT NULL AND retention_until<=%s
                       ORDER BY retention_until,project_id,revision_id
                       FOR UPDATE SKIP LOCKED LIMIT %s""",
                    (now, limit - processed),
                ).fetchall()
                for project_id, revision_id, asset_id in due:
                    connection.execute(
                        """UPDATE dataset_store.asset_revisions SET state='expired',ready_at=NULL
                           WHERE project_id=%s AND revision_id=%s AND state='ready'""",
                        (project_id, revision_id),
                    )
                    connection.execute(
                        """UPDATE dataset_store.dataset_revisions SET state='expired',ready_at=NULL
                           WHERE project_id=%s AND asset_revision_id=%s AND state='ready'""",
                        (project_id, revision_id),
                    )
                    connection.execute(
                        """UPDATE dataset_store.assets SET state=
                             CASE WHEN EXISTS (
                               SELECT 1 FROM dataset_store.asset_revisions r
                               WHERE r.project_id=%s AND r.asset_id=%s AND r.state='ready'
                             ) THEN 'active' ELSE 'expired' END
                           WHERE project_id=%s AND asset_id=%s""",
                        (project_id, asset_id, project_id, asset_id),
                    )
                    self._audit(
                        connection,
                        run_id=run_id,
                        event_type="revision_expired",
                        result="completed",
                        project_id=project_id,
                        revision_id=revision_id,
                    )
                    expired += 1
                processed += len(due)
            remaining = limit - processed
            stale_storage = connection.execute(
                """SELECT operation_id FROM dataset_store.storage_operations
                   WHERE state IN ('reserved','writing','reconciling')
                     AND updated_at<=%s AND (reconcile_after IS NULL OR reconcile_after<=%s)
                   ORDER BY updated_at,operation_id LIMIT %s""",
                (stale_before, now, remaining),
            ).fetchall() if remaining else []
            processed += len(stale_storage)
            remaining = limit - processed
            stale_deletions = connection.execute(
                """SELECT operation_id FROM dataset_store.lifecycle_operations
                   WHERE state IN ('marked','removing','reconciling')
                     AND (lease_expires_at IS NULL OR lease_expires_at<=%s)
                   ORDER BY updated_at,operation_id LIMIT %s""",
                (now, remaining),
            ).fetchall() if remaining else []
            processed += len(stale_deletions)

        for (operation_id,) in stale_storage:
            try:
                outcome = self._persistence.reconcile(operation_id, trusted_local=True)
                if outcome.state == "ready":
                    storage_ready += 1
                    event_type = "storage_reconciled_ready"
                else:
                    storage_failed += 1
                    event_type = "storage_reconciled_failed"
                with self._connection() as connection:
                    self._audit(
                        connection,
                        run_id=run_id,
                        operation_id=operation_id,
                        event_type=event_type,
                        result="completed",
                    )
            except PersistenceFailure as exc:
                deferred += 1
                with self._connection() as connection:
                    self._audit(
                        connection,
                        run_id=run_id,
                        operation_id=operation_id,
                        event_type="storage_reconcile_deferred",
                        result="failed",
                        error_code=exc.code,
                    )
        for (operation_id,) in stale_deletions:
            try:
                outcome = self.resume_deletion(operation_id, trusted_local=True)
                if outcome.state == "completed":
                    deleted += 1
                else:
                    deferred += 1
            except PersistenceFailure:
                deferred += 1

        remaining = limit - processed
        cleanup_truncated = False
        if remaining:
            with self._connection() as connection, self._maintenance_lock(
                connection, exclusive=True
            ):
                references = {
                    row[0]
                    for row in connection.execute(
                        """SELECT storage_ref FROM dataset_store.asset_revisions WHERE storage_ref IS NOT NULL
                           UNION SELECT storage_ref FROM dataset_store.dataset_revisions WHERE storage_ref IS NOT NULL"""
                    ).fetchall()
                }
                (
                    orphan_count,
                    staging_count,
                    orphan_logical,
                    physical,
                    cleanup_truncated,
                ) = self._blobs.cleanup_unreferenced(references, remaining)
                cleaned = orphan_count + staging_count
                processed += cleaned
                self._audit(
                    connection,
                    run_id=run_id,
                    event_type="garbage_collection",
                    result="completed",
                    item_count=cleaned,
                    logical_bytes=orphan_logical,
                    physical_bytes=physical,
                )
        with self._connection() as connection:
            more_due = connection.execute(
                """SELECT EXISTS(
                     SELECT 1 FROM dataset_store.asset_revisions
                     WHERE state='ready' AND retention_until IS NOT NULL AND retention_until<=%s
                   ) OR EXISTS(
                     SELECT 1 FROM dataset_store.storage_operations
                     WHERE state IN ('reserved','writing','reconciling') AND updated_at<=%s
                       AND (reconcile_after IS NULL OR reconcile_after<=%s)
                   ) OR EXISTS(
                     SELECT 1 FROM dataset_store.lifecycle_operations
                     WHERE state IN ('marked','removing','reconciling')
                       AND (lease_expires_at IS NULL OR lease_expires_at<=%s)
                   )""",
                (now, stale_before, now, now),
            ).fetchone()[0]
            self._audit(
                connection,
                run_id=run_id,
                event_type="maintenance_run",
                result="completed",
                item_count=processed,
                logical_bytes=orphan_logical,
                physical_bytes=physical,
            )
        return MaintenanceResult(
            run_id=run_id,
            policy_version=self._settings.policy_version,
            requested_limit=limit,
            processed_items=processed,
            expired_revisions=expired,
            storage_ready=storage_ready,
            storage_failed=storage_failed,
            deletions_completed=deleted,
            deletions_deferred=deferred,
            orphan_blobs_removed=orphan_count,
            staging_files_removed=staging_count,
            cleanup_file_bytes_removed=orphan_logical,
            physical_bytes_reclaimed=physical,
            truncated=bool(more_due or cleanup_truncated),
            completed_at=datetime.now(timezone.utc),
        )

    def _claim_deletion(self, connection, operation_id, owner):
        with connection.transaction():
            row = connection.execute(
                """SELECT o.state,o.operation_id,o.logical_bytes,o.quota_released_bytes,
                          o.fence_token,o.lease_owner,o.lease_expires_at,
                          a.storage_ref,d.storage_ref
                   FROM dataset_store.lifecycle_operations o
                   JOIN dataset_store.asset_revisions a
                     ON (a.project_id,a.revision_id)=(o.project_id,o.asset_revision_id)
                   JOIN dataset_store.dataset_revisions d
                     ON (d.project_id,d.asset_revision_id)=(a.project_id,a.revision_id)
                   WHERE o.operation_id=%s FOR UPDATE OF o,a,d""",
                (operation_id,),
            ).fetchone()
            if not row:
                raise PersistenceFailure("lifecycle_operation_not_found")
            if row[0] == "completed":
                return ("completed", row[1], row[0], row[2], row[3])
            if row[6] is not None and row[6] > datetime.now(timezone.utc) and row[5] != owner:
                raise PersistenceFailure("deletion_lease_active")
            fence = row[4] + 1
            connection.execute(
                """UPDATE dataset_store.lifecycle_operations
                   SET state='removing',lease_owner=%s,lease_expires_at=now()+(%s*interval '1 second'),
                       fence_token=%s,attempt_count=attempt_count+1,last_error_code=NULL,updated_at=now()
                   WHERE operation_id=%s""",
                (owner, self._settings.lease_seconds, fence, operation_id),
            )
            return ("removing", fence, row[7], row[8], row[2])

    def _complete_deletion(
        self, connection, operation_id, project_id, revision_id, owner, fence, logical_bytes
    ):
        with connection.transaction():
            operation = connection.execute(
                """SELECT asset_id,state,logical_bytes FROM dataset_store.lifecycle_operations
                   WHERE operation_id=%s AND lease_owner=%s AND fence_token=%s FOR UPDATE""",
                (operation_id, owner, fence),
            ).fetchone()
            if not operation or operation[1] != "removing" or operation[2] != logical_bytes:
                raise PersistenceFailure("stale_deletion_worker")
            project = connection.execute(
                "SELECT used_bytes FROM dataset_store.projects WHERE project_id=%s FOR UPDATE",
                (project_id,),
            ).fetchone()
            if not project or project[0] < logical_bytes:
                connection.execute(
                    """UPDATE dataset_store.lifecycle_operations
                       SET state='reconciling',lease_owner=NULL,lease_expires_at=NULL,
                           last_error_code='quota_inconsistent',updated_at=now()
                       WHERE operation_id=%s""",
                    (operation_id,),
                )
                raise PersistenceFailure("quota_inconsistent")
            connection.execute(
                """UPDATE dataset_store.asset_revisions
                   SET state='deleted',storage_ref=NULL,ready_at=NULL
                   WHERE project_id=%s AND revision_id=%s AND state='deleting'""",
                (project_id, revision_id),
            )
            connection.execute(
                """UPDATE dataset_store.dataset_revisions
                   SET state='deleted',storage_ref=NULL,ready_at=NULL
                   WHERE project_id=%s AND asset_revision_id=%s AND state='deleting'""",
                (project_id, revision_id),
            )
            connection.execute(
                """UPDATE dataset_store.projects SET used_bytes=used_bytes-%s,updated_at=now()
                   WHERE project_id=%s""",
                (logical_bytes, project_id),
            )
            connection.execute(
                """UPDATE dataset_store.lifecycle_operations
                   SET state='completed',quota_released_bytes=logical_bytes,
                       lease_owner=NULL,lease_expires_at=NULL,last_error_code=NULL,
                       updated_at=now(),completed_at=now()
                   WHERE operation_id=%s AND lease_owner=%s AND fence_token=%s""",
                (operation_id, owner, fence),
            )
            connection.execute(
                """UPDATE dataset_store.assets SET state=CASE
                     WHEN EXISTS (SELECT 1 FROM dataset_store.asset_revisions r WHERE r.project_id=%s AND r.asset_id=%s AND r.state='ready') THEN 'active'
                     WHEN EXISTS (SELECT 1 FROM dataset_store.asset_revisions r WHERE r.project_id=%s AND r.asset_id=%s AND r.state='expired') THEN 'expired'
                     WHEN EXISTS (SELECT 1 FROM dataset_store.asset_revisions r WHERE r.project_id=%s AND r.asset_id=%s AND r.state='deleting') THEN 'deleting'
                     ELSE 'deleted' END
                   WHERE project_id=%s AND asset_id=%s""",
                (project_id, operation[0], project_id, operation[0], project_id, operation[0], project_id, operation[0]),
            )
            self._audit(
                connection,
                event_type="deletion_completed",
                result="completed",
                project_id=project_id,
                revision_id=revision_id,
                operation_id=operation_id,
                logical_bytes=logical_bytes,
            )
        return LifecycleOperationResult(
            operation_id=operation_id,
            project_id=project_id,
            revision_id=revision_id,
            state="completed",
            logical_bytes=logical_bytes,
            quota_released_bytes=logical_bytes,
        )

    def _defer_deletion(self, connection, operation_id, owner, fence, error_code):
        with connection.transaction():
            row = connection.execute(
                """UPDATE dataset_store.lifecycle_operations
                   SET state='reconciling',lease_owner=NULL,lease_expires_at=NULL,
                       last_error_code=%s,updated_at=now()
                   WHERE operation_id=%s AND lease_owner=%s AND fence_token=%s
                   RETURNING project_id,asset_revision_id""",
                (error_code, operation_id, owner, fence),
            ).fetchone()
            if row:
                self._audit(
                    connection,
                    event_type="deletion_deferred",
                    result="failed",
                    project_id=row[0],
                    revision_id=row[1],
                    operation_id=operation_id,
                    error_code=error_code,
                )

    def _audit(
        self,
        connection,
        *,
        event_type,
        result,
        run_id=None,
        project_id=None,
        revision_id=None,
        operation_id=None,
        item_count=0,
        logical_bytes=0,
        physical_bytes=0,
        error_code=None,
    ):
        connection.execute(
            """INSERT INTO dataset_store.lifecycle_audit_events
               (event_id,run_id,project_id,asset_revision_id,operation_id,event_type,result,
                item_count,logical_bytes,physical_bytes,error_code)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                uuid.uuid4(), run_id, project_id, revision_id, operation_id, event_type, result,
                item_count, logical_bytes, physical_bytes, error_code,
            ),
        )

    @staticmethod
    def _validate_ids(project_id, revision_id):
        if type(project_id) is not uuid.UUID or type(revision_id) is not uuid.UUID:
            raise PersistenceFailure("invalid_lifecycle_identity")

    @staticmethod
    def _operation_result(row, project_id, revision_id):
        return LifecycleOperationResult(
            operation_id=row[0],
            project_id=project_id,
            revision_id=revision_id,
            state=row[1],
            logical_bytes=row[2],
            quota_released_bytes=row[3],
        )
