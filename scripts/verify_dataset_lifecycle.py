"""Reproduce PV2-02b evidence in a disposable database and temporary private store."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import uuid
import sys

import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.datasets import (
    Column,
    DatasetLifecycleSettings,
    DatasetPersistenceSettings,
    FileSpec,
    ParseSettings,
    PrivateDatasetLifecycleService,
    PrivateDatasetPersistenceService,
    PrivateLocalBlobStore,
)
from scripts.migrate import apply_migrations, connect_kwargs


class SyntheticCrash(BaseException):
    pass


def main() -> None:
    admin = connect_kwargs()
    database_name = "eac_verify_dataset_lifecycle_" + uuid.uuid4().hex
    with psycopg.connect(**admin, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    database = {**admin, "dbname": database_name}
    try:
        with psycopg.connect(**database, autocommit=True) as connection:
            apply_migrations(connection)

        @contextmanager
        def connections():
            connection = psycopg.connect(**database, autocommit=True)
            try:
                yield connection
            finally:
                connection.close()

        with tempfile.TemporaryDirectory() as temporary:
            store = PrivateLocalBlobStore(Path(temporary) / "private")
            persistence = PrivateDatasetPersistenceService(
                connections,
                store,
                settings=DatasetPersistenceSettings(enabled=True),
            )
            lifecycle = PrivateDatasetLifecycleService(
                connections,
                store,
                settings=DatasetLifecycleSettings(enabled=True, max_items_per_run=20),
            )
            project_id = uuid.uuid4()
            persistence.create_project(
                project_id=project_id,
                scope_key="synthetic:lifecycle-evidence",
                quota_bytes=1_000_000,
                lifecycle_policy_version="local-retention-v1",
                trusted_local=True,
            )
            spec = FileSpec(
                project_id=str(project_id),
                dataset_id="synthetic_sales",
                filename="synthetic.csv",
                columns=(Column(name="region"), Column(name="amount", kind="money", unit="USD")),
            )

            def persist(amount, asset_key, service=persistence):
                return service.persist(
                    f"region,amount\nnorth,{amount}\n".encode(),
                    spec,
                    ParseSettings(enabled=True),
                    project_id=project_id,
                    asset_key=asset_key,
                    trusted_local=True,
                )

            expiring = persist("10.50", "expiring")
            deleting = persist("11.50", "deleting")
            before_delete_used = deleting.project.used_bytes
            deletion = lifecycle.mark_for_deletion(
                project_id, deleting.asset_revision.revision_id, trusted_local=True
            )
            deleted = lifecycle.resume_deletion(deletion.operation_id, trusted_local=True)
            with connections() as connection:
                after_delete_used = connection.execute(
                    "SELECT used_bytes FROM dataset_store.projects WHERE project_id=%s", (project_id,)
                ).fetchone()[0]
            lifecycle.set_retention(
                project_id,
                expiring.asset_revision.revision_id,
                datetime.now(timezone.utc) - timedelta(minutes=1),
                trusted_local=True,
            )

            def crash():
                raise SyntheticCrash("synthetic")

            interrupted = PrivateDatasetPersistenceService(
                connections,
                store,
                settings=DatasetPersistenceSettings(enabled=True),
                after_reserve=crash,
            )
            try:
                persist("12.50", "interrupted", interrupted)
            except SyntheticCrash:
                pass
            with connections() as connection:
                connection.execute(
                    """UPDATE dataset_store.storage_operations
                       SET created_at=now()-interval '3 hours',updated_at=now()-interval '2 hours',
                           reconcile_after=now()-interval '1 hour'
                       WHERE state='writing'"""
                )
            orphan = store.new_reference("raw")
            store.put(orphan, b"synthetic-orphan")
            shard = store._path(store.new_reference("dataset")).parent
            (shard / ".staging-abandoned").write_bytes(b"synthetic-staging")
            now = datetime.now(timezone.utc)
            result = lifecycle.run_maintenance(
                now=now,
                stale_before=now - timedelta(minutes=30),
                limit=20,
                trusted_local=True,
            )
            with connections() as connection:
                used, reserved = connection.execute(
                    "SELECT used_bytes,reserved_bytes FROM dataset_store.projects WHERE project_id=%s",
                    (project_id,),
                ).fetchone()
                states = connection.execute(
                    "SELECT state,count(*) FROM dataset_store.asset_revisions GROUP BY state ORDER BY state"
                ).fetchall()
                audit_types = connection.execute(
                    "SELECT event_type,count(*) FROM dataset_store.lifecycle_audit_events GROUP BY event_type ORDER BY event_type"
                ).fetchall()
            evidence = {
                "audit_event_counts": [{"event": row[0], "count": row[1]} for row in audit_types],
                "deletion_quota_released_bytes": deleted.quota_released_bytes,
                "evidence_version": "pv2-02b-lifecycle-v1",
                "expired_revisions": result.expired_revisions,
                "logical_quota_after_delete_before_maintenance": after_delete_used,
                "logical_quota_before_delete": before_delete_used,
                "maintenance_processed_items": result.processed_items,
                "orphan_blobs_removed": result.orphan_blobs_removed,
                "paid_calls": 0,
                "physical_bytes_reclaimed_positive": result.physical_bytes_reclaimed > 0,
                "private_blob_count_after_maintenance": store.object_count(),
                "quota_reserved_bytes": reserved,
                "quota_used_bytes": used,
                "revision_states": [{"state": row[0], "count": row[1]} for row in states],
                "scope": "trusted local synthetic harness; no real-user time-savings claim",
                "staging_files_removed": result.staging_files_removed,
                "stale_storage_failed": result.storage_failed,
                "truncated": result.truncated,
                "user_restore_after_physical_delete_supported": False,
            }
            print(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        with psycopg.connect(**admin, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name)))


if __name__ == "__main__":
    main()
