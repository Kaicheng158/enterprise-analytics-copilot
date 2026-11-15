"""PV2-02b lifecycle tests use one disposable database and synthetic blobs only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
import tempfile
import unittest
import uuid

import psycopg
from psycopg import sql

from backend.datasets import (
    Column,
    DatasetLifecycleSettings,
    DatasetPersistenceSettings,
    FileSpec,
    ParseSettings,
    PersistenceFailure,
    PrivateDatasetLifecycleService,
    PrivateDatasetPersistenceService,
    PrivateLocalBlobStore,
)
from scripts.migrate import apply_migrations, connect_kwargs


class SyntheticProcessCrash(BaseException):
    pass


class FailingDeleteStore(PrivateLocalBlobStore):
    def remove(self, reference):
        raise PersistenceFailure("synthetic_delete_failure")


class PrivateDatasetLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = connect_kwargs()
        cls.database_name = "eac_test_dataset_lifecycle_" + uuid.uuid4().hex
        with psycopg.connect(**cls.admin, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.database_name)))
        cls.addClassCleanup(cls.cleanup_database)
        cls.database = {**cls.admin, "dbname": cls.database_name}
        with psycopg.connect(**cls.database, autocommit=True) as connection:
            apply_migrations(connection)

    @classmethod
    def cleanup_database(cls):
        with psycopg.connect(**cls.admin, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(cls.database_name)))

    def setUp(self):
        with self.connections() as connection:
            connection.execute("TRUNCATE dataset_store.projects CASCADE")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = PrivateLocalBlobStore(Path(self.temporary.name) / "private")
        self.project_id = uuid.uuid4()
        self.persistence = self.new_persistence(self.store)
        self.lifecycle = self.new_lifecycle(self.store)
        self.persistence.create_project(
            project_id=self.project_id,
            scope_key="synthetic:lifecycle",
            quota_bytes=1_000_000,
            lifecycle_policy_version="local-retention-v1",
            trusted_local=True,
        )

    @contextmanager
    def connections(self):
        connection = psycopg.connect(**self.database, autocommit=True)
        try:
            yield connection
        finally:
            connection.close()

    def new_persistence(self, store, **kwargs):
        return PrivateDatasetPersistenceService(
            self.connections,
            store,
            settings=DatasetPersistenceSettings(enabled=True),
            **kwargs,
        )

    def new_lifecycle(self, store, **kwargs):
        return PrivateDatasetLifecycleService(
            self.connections,
            store,
            settings=DatasetLifecycleSettings(enabled=True, max_items_per_run=20, lease_seconds=1),
            **kwargs,
        )

    def specification(self, project_id=None):
        return FileSpec(
            project_id=str(project_id or self.project_id),
            dataset_id="synthetic_sales",
            filename="synthetic.csv",
            columns=(Column(name="region"), Column(name="amount", kind="money", unit="USD")),
        )

    def persist(self, amount="10.50", *, project_id=None, asset_key="sales", service=None):
        project_id = project_id or self.project_id
        return (service or self.persistence).persist(
            f"region,amount\nnorth,{amount}\n".encode(),
            self.specification(project_id),
            ParseSettings(enabled=True),
            project_id=project_id,
            asset_key=asset_key,
            trusted_local=True,
        )

    def quota(self, project_id=None):
        with self.connections() as connection:
            return connection.execute(
                "SELECT used_bytes,reserved_bytes FROM dataset_store.projects WHERE project_id=%s",
                (project_id or self.project_id,),
            ).fetchone()

    def test_migration_contract_and_public_access_revoked(self):
        with self.connections() as connection:
            applied = connection.execute(
                "SELECT count(*) FROM app_migrations.applied WHERE name='008_private_dataset_lifecycle.sql'"
            ).fetchone()[0]
            tables = connection.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='dataset_store'"
            ).fetchone()[0]
            columns = connection.execute(
                """SELECT count(*) FROM information_schema.columns
                   WHERE table_schema='dataset_store' AND table_name='storage_operations'
                     AND column_name IN ('state_version','reconcile_after')"""
            ).fetchone()[0]
            privileges = connection.execute(
                """SELECT has_table_privilege('public','dataset_store.lifecycle_operations','SELECT'),
                          has_table_privilege('public','dataset_store.lifecycle_audit_events','SELECT')"""
            ).fetchone()
        self.assertEqual((applied, tables, columns, privileges), (1, 7, 2, (False, False)))

    def test_expiration_blocks_reads_without_releasing_quota(self):
        stored = self.persist()
        used_before = self.quota()[0]
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        self.lifecycle.set_retention(
            self.project_id, stored.asset_revision.revision_id, past, trusted_local=True
        )
        result = self.lifecycle.run_maintenance(
            now=datetime.now(timezone.utc),
            stale_before=past,
            limit=10,
            trusted_local=True,
        )
        self.assertEqual(result.expired_revisions, 1)
        with self.assertRaisesRegex(PersistenceFailure, "revision_unavailable"):
            self.lifecycle.require_readable(
                self.project_id, stored.asset_revision.revision_id, trusted_local=True
            )
        self.assertEqual(self.quota(), (used_before, 0))
        self.assertEqual(self.store.object_count(), 2)

    def test_delete_is_idempotent_and_releases_quota_once(self):
        stored = self.persist()
        revision_id = stored.asset_revision.revision_id
        marked = self.lifecycle.mark_for_deletion(
            self.project_id, revision_id, trusted_local=True
        )
        with self.assertRaisesRegex(PersistenceFailure, "revision_unavailable"):
            self.lifecycle.require_readable(self.project_id, revision_id, trusted_local=True)
        duplicate_mark = self.lifecycle.mark_for_deletion(
            self.project_id, revision_id, trusted_local=True
        )
        self.assertEqual(duplicate_mark.operation_id, marked.operation_id)
        completed = self.lifecycle.resume_deletion(marked.operation_id, trusted_local=True)
        repeated = self.lifecycle.resume_deletion(marked.operation_id, trusted_local=True)
        self.assertEqual(completed.state, "completed")
        self.assertEqual(repeated.state, "completed")
        self.assertEqual(self.quota(), (0, 0))
        self.assertEqual(self.store.object_count(), 0)
        with self.connections() as connection:
            states = connection.execute(
                """SELECT a.state,d.state,o.state,o.quota_released_bytes
                   FROM dataset_store.asset_revisions a
                   JOIN dataset_store.dataset_revisions d ON d.asset_revision_id=a.revision_id
                   JOIN dataset_store.lifecycle_operations o ON o.asset_revision_id=a.revision_id"""
            ).fetchone()
        self.assertEqual(states[:3], ("deleted", "deleted", "completed"))
        self.assertEqual(states[3], completed.logical_bytes)

    def test_crash_after_first_blob_delete_resumes_without_double_release(self):
        stored = self.persist()
        marked = self.lifecycle.mark_for_deletion(
            self.project_id, stored.asset_revision.revision_id, trusted_local=True
        )

        def crash():
            raise SyntheticProcessCrash("synthetic abrupt process exit")

        crashing = self.new_lifecycle(self.store, after_first_blob_delete=crash)
        with self.assertRaises(SyntheticProcessCrash):
            crashing.resume_deletion(marked.operation_id, trusted_local=True)
        self.assertEqual(self.store.object_count(), 1)
        self.assertGreater(self.quota()[0], 0)
        with self.connections() as connection:
            connection.execute(
                "UPDATE dataset_store.lifecycle_operations SET lease_expires_at=now()-interval '1 second' WHERE operation_id=%s",
                (marked.operation_id,),
            )
        recovered = self.lifecycle.resume_deletion(marked.operation_id, trusted_local=True)
        self.assertEqual(recovered.state, "completed")
        self.assertEqual(self.quota(), (0, 0))
        self.assertEqual(self.store.object_count(), 0)

    def test_disk_delete_failure_stays_unreadable_and_keeps_quota(self):
        stored = self.persist()
        marked = self.lifecycle.mark_for_deletion(
            self.project_id, stored.asset_revision.revision_id, trusted_local=True
        )
        failing = self.new_lifecycle(FailingDeleteStore(self.store._root))
        with self.assertRaisesRegex(PersistenceFailure, "deletion_incomplete"):
            failing.resume_deletion(marked.operation_id, trusted_local=True)
        self.assertGreater(self.quota()[0], 0)
        with self.connections() as connection:
            state, error = connection.execute(
                "SELECT state,last_error_code FROM dataset_store.lifecycle_operations WHERE operation_id=%s",
                (marked.operation_id,),
            ).fetchone()
        self.assertEqual((state, error), ("reconciling", "blob_delete_failed"))
        with self.assertRaises(PersistenceFailure):
            self.lifecycle.require_readable(
                self.project_id, stored.asset_revision.revision_id, trusted_local=True
            )

    def test_concurrent_deletion_workers_do_not_double_release(self):
        stored = self.persist()
        marked = self.lifecycle.mark_for_deletion(
            self.project_id, stored.asset_revision.revision_id, trusted_local=True
        )

        def run(_):
            try:
                return self.lifecycle.resume_deletion(marked.operation_id, trusted_local=True).state
            except PersistenceFailure as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(run, range(2)))
        self.assertIn("completed", outcomes)
        self.assertTrue(all(value in {"completed", "deletion_lease_active"} for value in outcomes))
        self.assertEqual(self.quota(), (0, 0))

    def test_deletion_waits_for_project_write_and_preserves_other_ready_revision(self):
        first = self.persist("10.50")
        writer_entered, writer_release, delete_started = Event(), Event(), Event()

        def wait_after_reserve():
            writer_entered.set()
            writer_release.wait(5)

        writer_service = self.new_persistence(self.store, after_reserve=wait_after_reserve)

        def write_revision():
            return self.persist("11.50", service=writer_service)

        def mark_old_revision():
            delete_started.set()
            return self.lifecycle.mark_for_deletion(
                self.project_id, first.asset_revision.revision_id, trusted_local=True
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            writing = pool.submit(write_revision)
            self.assertTrue(writer_entered.wait(5))
            deleting = pool.submit(mark_old_revision)
            self.assertTrue(delete_started.wait(5))
            with self.assertRaises(TimeoutError):
                deleting.result(timeout=0.1)
            writer_release.set()
            second = writing.result(timeout=5)
            operation = deleting.result(timeout=5)
        self.lifecycle.require_readable(
            self.project_id, second.asset_revision.revision_id, trusted_local=True
        )
        self.lifecycle.resume_deletion(operation.operation_id, trusted_local=True)
        self.lifecycle.require_readable(
            self.project_id, second.asset_revision.revision_id, trusted_local=True
        )
        with self.connections() as connection:
            asset_state = connection.execute(
                "SELECT state FROM dataset_store.assets WHERE project_id=%s AND asset_id=%s",
                (self.project_id, second.asset.asset_id),
            ).fetchone()[0]
        self.assertEqual(asset_state, "active")

    def test_garbage_collection_preserves_references_and_removes_orphans(self):
        self.persist()
        orphan = self.store.new_reference("raw")
        self.store.put(orphan, b"orphan-synthetic")
        shard = self.store._path(self.store.new_reference("dataset")).parent
        (shard / ".staging-abandoned").write_bytes(b"partial")
        now = datetime.now(timezone.utc)
        result = self.lifecycle.run_maintenance(
            now=now, stale_before=now - timedelta(hours=1), limit=10, trusted_local=True
        )
        self.assertEqual((result.orphan_blobs_removed, result.staging_files_removed), (1, 1))
        self.assertEqual(self.store.object_count(), 2)
        self.assertGreater(result.physical_bytes_reclaimed, 0)

    def test_stale_storage_operation_is_reconciled_but_active_writer_blocks_maintenance(self):
        def crash():
            raise SyntheticProcessCrash("synthetic")

        interrupted = self.new_persistence(self.store, after_reserve=crash)
        with self.assertRaises(SyntheticProcessCrash):
            self.persist(service=interrupted)
        with self.connections() as connection:
            connection.execute(
                """UPDATE dataset_store.storage_operations
                   SET created_at=now()-interval '3 hours',updated_at=now()-interval '2 hours',
                       reconcile_after=now()-interval '1 hour'"""
            )
        now = datetime.now(timezone.utc)
        result = self.lifecycle.run_maintenance(
            now=now, stale_before=now - timedelta(minutes=30), limit=10, trusted_local=True
        )
        self.assertEqual(result.storage_failed, 1)
        self.assertEqual(self.quota(), (0, 0))

        entered, release = Event(), Event()

        def hold():
            def wait_after_reserve():
                entered.set()
                release.wait(5)
            service = self.new_persistence(self.store, after_reserve=wait_after_reserve)
            return self.persist("11.50", asset_key="active", service=service)

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(hold)
            self.assertTrue(entered.wait(5))
            with self.assertRaisesRegex(PersistenceFailure, "maintenance_busy"):
                self.lifecycle.run_maintenance(
                    now=datetime.now(timezone.utc),
                    stale_before=datetime.now(timezone.utc) - timedelta(hours=1),
                    limit=1,
                    trusted_local=True,
                )
            release.set()
            self.assertEqual(future.result().asset_revision.state, "ready")

    def test_maintenance_limit_is_deterministic_and_reports_truncation(self):
        first = self.persist("10.50", asset_key="one")
        second = self.persist("11.50", asset_key="two")
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        for revision in (first.asset_revision.revision_id, second.asset_revision.revision_id):
            self.lifecycle.set_retention(self.project_id, revision, past, trusted_local=True)
        result = self.lifecycle.run_maintenance(
            now=datetime.now(timezone.utc), stale_before=past, limit=1, trusted_local=True
        )
        self.assertEqual((result.processed_items, result.expired_revisions, result.truncated), (1, 1, True))

    def test_cross_project_delete_never_affects_other_project(self):
        first = self.persist()
        other = uuid.uuid4()
        self.persistence.create_project(
            project_id=other,
            scope_key="synthetic:other",
            quota_bytes=1_000_000,
            lifecycle_policy_version="local-retention-v1",
            trusted_local=True,
        )
        second = self.persist(project_id=other)
        operation = self.lifecycle.mark_for_deletion(
            self.project_id, first.asset_revision.revision_id, trusted_local=True
        )
        self.lifecycle.resume_deletion(operation.operation_id, trusted_local=True)
        self.lifecycle.require_readable(other, second.asset_revision.revision_id, trusted_local=True)
        self.assertEqual(self.store.object_count(), 2)
        self.assertGreater(self.quota(other)[0], 0)

    def test_default_off_trust_validation_and_database_failure(self):
        disabled = PrivateDatasetLifecycleService(self.connections, self.store)
        now = datetime.now(timezone.utc)
        with self.assertRaisesRegex(PersistenceFailure, "lifecycle_disabled"):
            disabled.run_maintenance(
                now=now,
                stale_before=now,
                limit=1,
                trusted_local=True,
            )
        with self.assertRaisesRegex(PersistenceFailure, "trusted_local_required"):
            self.lifecycle.run_maintenance(
                now=now,
                stale_before=now,
                limit=1,
            )

        @contextmanager
        def broken():
            raise RuntimeError("private database detail")
            yield

        service = PrivateDatasetLifecycleService(
            broken,
            self.store,
            settings=DatasetLifecycleSettings(enabled=True),
        )
        with self.assertRaisesRegex(PersistenceFailure, "database_error"):
            service.run_maintenance(
                now=now,
                stale_before=now,
                limit=1,
                trusted_local=True,
            )

    def test_audit_is_content_free_and_contains_lifecycle_events(self):
        stored = self.persist()
        operation = self.lifecycle.mark_for_deletion(
            self.project_id, stored.asset_revision.revision_id, trusted_local=True
        )
        self.lifecycle.resume_deletion(operation.operation_id, trusted_local=True)
        with self.connections() as connection:
            events = connection.execute(
                """SELECT event_type,result,error_code,item_count,logical_bytes,physical_bytes
                   FROM dataset_store.lifecycle_audit_events ORDER BY created_at,event_type"""
            ).fetchall()
        self.assertEqual({row[0] for row in events}, {"deletion_marked", "deletion_completed"})
        self.assertTrue(all(len(row) == 6 for row in events))


if __name__ == "__main__":
    unittest.main()
