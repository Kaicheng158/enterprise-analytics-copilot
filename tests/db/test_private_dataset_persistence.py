"""PV2-02a tests create/drop one uniquely named database and temporary blob root."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
import uuid

import psycopg
from psycopg import sql

from backend.datasets import Column, FileSpec, ParseSettings
from backend.datasets.persistence import PrivateDatasetPersistenceService, _dataset_bytes
from backend.datasets.persistence_models import DatasetPersistenceSettings, PersistenceFailure
from backend.datasets.private_storage import PrivateLocalBlobStore
from backend.datasets.service import parse_file
from scripts.migrate import apply_migrations, connect_kwargs
from tests.dataset_fixtures import workbook


class FailingBlobStore(PrivateLocalBlobStore):
    def put(self, reference, content):
        if reference.endswith(":dataset"):
            raise PersistenceFailure("synthetic_storage_failure")
        return super().put(reference, content)


class FailingCleanupStore(FailingBlobStore):
    def remove(self, reference):
        raise PersistenceFailure("synthetic_cleanup_failure")


class SyntheticProcessCrash(BaseException):
    """Fault injection that bypasses ordinary in-process exception cleanup."""


class PrivateDatasetPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = connect_kwargs()
        cls.database_name = "eac_test_dataset_store_" + uuid.uuid4().hex
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
        with psycopg.connect(**self.database, autocommit=True) as connection:
            connection.execute("TRUNCATE dataset_store.projects CASCADE")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = PrivateLocalBlobStore(Path(self.temporary.name) / "private")
        self.project_id = uuid.uuid4()
        self.service = self.new_service(self.store)
        self.service.create_project(project_id=self.project_id, scope_key="synthetic:test",
            quota_bytes=1_000_000, lifecycle_policy_version="local-retention-v1", trusted_local=True)

    @contextmanager
    def connections(self):
        connection = psycopg.connect(**self.database, autocommit=True)
        try:
            yield connection
        finally:
            connection.close()

    def new_service(self, store=None, **kwargs):
        return PrivateDatasetPersistenceService(self.connections, store or self.store,
            settings=DatasetPersistenceSettings(enabled=True), **kwargs)

    def specification(self, project_id=None, extension="csv"):
        return FileSpec(project_id=str(project_id or self.project_id), dataset_id="synthetic_sales",
            filename="synthetic."+extension, columns=(Column(name="region"), Column(name="amount",kind="money",unit="USD")))

    def persist(self, source=b"region,amount\nnorth,10.50\n", *, service=None, project_id=None, asset_key="sales"):
        project_id = project_id or self.project_id
        return (service or self.service).persist(source, self.specification(project_id),
            ParseSettings(enabled=True), project_id=project_id, asset_key=asset_key, trusted_local=True)

    def counts(self):
        with self.connections() as connection:
            return connection.execute("""SELECT
                (SELECT count(*) FROM dataset_store.asset_revisions),
                (SELECT count(*) FROM dataset_store.dataset_revisions),
                (SELECT count(*) FROM dataset_store.storage_operations),
                (SELECT used_bytes FROM dataset_store.projects WHERE project_id=%s),
                (SELECT reserved_bytes FROM dataset_store.projects WHERE project_id=%s)""",
                (self.project_id,self.project_id)).fetchone()

    def test_migration_contract_and_public_access_revoked(self):
        with self.connections() as connection:
            applied = connection.execute("SELECT count(*) FROM app_migrations.applied WHERE name='007_private_dataset_storage.sql'").fetchone()[0]
            tables = connection.execute("""SELECT count(*) FROM information_schema.tables
                WHERE table_schema='dataset_store' AND table_name IN
                ('projects','assets','asset_revisions','dataset_revisions','storage_operations')""").fetchone()[0]
            privileges = connection.execute("SELECT has_schema_privilege('public','dataset_store','USAGE'),has_table_privilege('public','dataset_store.projects','SELECT')").fetchone()
        self.assertEqual((applied,tables,privileges),(1,5,(False,False)))

    def test_normal_save_duplicate_and_changed_revision(self):
        first = self.persist()
        used = first.project.used_bytes
        duplicate = self.persist()
        changed = self.persist(b"region,amount\nnorth,11.50\n")
        self.assertFalse(first.reused)
        self.assertTrue(duplicate.reused)
        self.assertEqual(first.asset_revision.revision_id, duplicate.asset_revision.revision_id)
        self.assertEqual(changed.asset_revision.revision_number, 2)
        self.assertNotEqual(first.asset_revision.revision_id, changed.asset_revision.revision_id)
        self.assertEqual(self.store.object_count(), 4)
        self.assertEqual(self.counts()[:3], (2,2,2))
        self.assertGreater(changed.project.used_bytes, used)
        with self.connections() as connection:
            states = connection.execute("SELECT state FROM dataset_store.asset_revisions ORDER BY revision_number").fetchall()
        self.assertEqual(states, [("ready",),("ready",)])

    def test_xlsx_source_uses_same_persistence_contract(self):
        source=workbook([["region","amount"],["华东","10.50"]])
        result=self.service.persist(source,self.specification(extension="xlsx"),ParseSettings(enabled=True),
            project_id=self.project_id,asset_key="xlsx_sales",trusted_local=True)
        self.assertEqual(result.asset_revision.source_format,"xlsx")
        self.assertEqual(result.asset_revision.source_bytes,len(source))
        self.assertEqual(result.asset_revision.state,"ready")
        self.assertEqual(self.store.object_count(),2)

    def test_cross_project_identity_and_storage_are_separate(self):
        first = self.persist()
        other = uuid.uuid4()
        self.service.create_project(project_id=other,scope_key="synthetic:other",quota_bytes=1_000_000,
            lifecycle_policy_version="local-retention-v1",trusted_local=True)
        second = self.persist(project_id=other)
        self.assertNotEqual(first.asset.asset_id,second.asset.asset_id)
        self.assertNotEqual(first.asset_revision.revision_id,second.asset_revision.revision_id)
        with self.connections() as connection:
            refs=connection.execute("SELECT storage_ref FROM dataset_store.asset_revisions ORDER BY project_id").fetchall()
        self.assertEqual(len({row[0] for row in refs}),2)

    def test_concurrent_duplicate_is_one_revision_and_one_quota_charge(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.persist(),range(2)))
        self.assertEqual(len({r.asset_revision.revision_id for r in results}),1)
        self.assertEqual(sum(not r.reused for r in results),1)
        self.assertEqual(self.counts()[:3],(1,1,1))
        self.assertEqual(self.store.object_count(),2)

    def test_project_wide_duplicate_with_different_asset_key_reuses_original(self):
        first=self.persist(asset_key="sales_primary")
        duplicate=self.persist(asset_key="sales_duplicate")
        self.assertTrue(duplicate.reused)
        self.assertEqual(duplicate.asset.asset_id,first.asset.asset_id)
        self.assertEqual(duplicate.asset.asset_key,"sales_primary")
        self.assertEqual(self.counts()[:3],(1,1,1))
        self.assertEqual(self.store.object_count(),2)

    def test_duplicate_never_reuses_missing_ready_blob(self):
        self.persist()
        with self.connections() as connection:
            reference=connection.execute("SELECT storage_ref FROM dataset_store.asset_revisions").fetchone()[0]
        self.store.remove(reference)
        with self.assertRaisesRegex(PersistenceFailure,"stored_blob_integrity_mismatch"):
            self.persist()

    def test_concurrent_project_wide_duplicate_across_asset_keys(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda key:self.persist(asset_key=key),("sales_one","sales_two")))
        self.assertEqual(len({r.asset_revision.revision_id for r in results}),1)
        self.assertEqual(sum(not r.reused for r in results),1)
        self.assertEqual(self.counts()[:3],(1,1,1))

    def test_concurrent_quota_reservation_never_overshoots(self):
        project=uuid.uuid4(); source=b"region,amount\nnorth,10.50\n"
        dataset=parse_file(source,self.specification(project),ParseSettings(enabled=True))
        one_size=len(source)+len(_dataset_bytes(dataset))
        self.service.create_project(project_id=project,scope_key="synthetic:quota",quota_bytes=one_size+10,
            lifecycle_policy_version="local-retention-v1",trusted_local=True)
        def submit(index):
            try:
                return self.persist(source.replace(b"10.50",f"{10+index}.50".encode()),project_id=project,asset_key=f"sales_{index}")
            except PersistenceFailure as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes=list(pool.map(submit,range(2)))
        self.assertEqual(sum(not isinstance(value,str) for value in outcomes),1)
        self.assertIn("quota_exceeded",outcomes)
        with self.connections() as connection:
            used,reserved,quota=connection.execute("SELECT used_bytes,reserved_bytes,quota_bytes FROM dataset_store.projects WHERE project_id=%s",(project,)).fetchone()
        self.assertEqual(reserved,0); self.assertLessEqual(used,quota)

    def test_storage_failure_releases_quota_and_never_publishes(self):
        root=Path(self.temporary.name)/"failure"
        failing=self.new_service(FailingBlobStore(root))
        with self.assertRaisesRegex(PersistenceFailure,"storage_write_failed"):
            self.persist(service=failing)
        self.assertEqual(self.counts(),(1,1,1,0,0))
        with self.connections() as connection:
            states=connection.execute("SELECT a.state,d.state,o.state,o.error_code FROM dataset_store.asset_revisions a JOIN dataset_store.dataset_revisions d ON d.asset_revision_id=a.revision_id JOIN dataset_store.storage_operations o ON o.asset_revision_id=a.revision_id").fetchone()
        self.assertEqual(states,("failed","failed","failed","storage_write_failed"))
        self.assertEqual(failing._blobs.object_count(),0)
        recovered=self.persist(service=self.new_service(PrivateLocalBlobStore(root)))
        self.assertEqual(recovered.asset_revision.state,"ready")
        self.assertFalse(recovered.reused)
        self.assertEqual(self.counts()[:3],(1,1,1))
        self.assertEqual(self.counts()[4],0)

    def test_uncertain_cleanup_stays_reserved_until_reconciled(self):
        root=Path(self.temporary.name)/"cleanup"
        failing=self.new_service(FailingCleanupStore(root))
        with self.assertRaisesRegex(PersistenceFailure,"storage_cleanup_uncertain"):
            self.persist(service=failing)
        with self.connections() as connection:
            operation,state=connection.execute("SELECT operation_id,state FROM dataset_store.storage_operations").fetchone()
            used,reserved=connection.execute("SELECT used_bytes,reserved_bytes FROM dataset_store.projects WHERE project_id=%s",(self.project_id,)).fetchone()
        self.assertEqual(state,"reconciling"); self.assertEqual(used,0); self.assertGreater(reserved,0)
        healthy=self.new_service(PrivateLocalBlobStore(root))
        result=healthy.reconcile(operation,trusted_local=True)
        self.assertEqual(result.state,"failed")
        self.assertEqual(self.counts()[3:],(0,0))

    def test_process_crash_after_reservation_never_publishes_and_reconciles(self):
        def crash():
            raise SyntheticProcessCrash("synthetic abrupt termination")

        interrupted = self.new_service(after_reserve=crash)
        with self.assertRaises(SyntheticProcessCrash):
            self.persist(service=interrupted)
        with self.connections() as connection:
            operation, operation_state, revision_state = connection.execute(
                """SELECT o.operation_id,o.state,a.state
                   FROM dataset_store.storage_operations o
                   JOIN dataset_store.asset_revisions a
                     ON (a.project_id,a.revision_id)=(o.project_id,o.asset_revision_id)"""
            ).fetchone()
            used, reserved = connection.execute(
                "SELECT used_bytes,reserved_bytes FROM dataset_store.projects WHERE project_id=%s",
                (self.project_id,),
            ).fetchone()
        self.assertEqual((operation_state, revision_state), ("writing", "pending"))
        self.assertEqual(used, 0)
        self.assertGreater(reserved, 0)
        self.assertEqual(self.store.object_count(), 0)

        result = self.service.reconcile(operation, trusted_local=True)
        self.assertEqual(
            (result.state, result.action),
            ("failed", "failed_missing_or_mismatched_blob"),
        )
        self.assertEqual(self.counts()[3:], (0, 0))
        self.assertEqual(self.store.object_count(), 0)

    def test_uncertain_finalization_requires_verified_reconciliation(self):
        def fail(): raise RuntimeError("synthetic database boundary failure")
        uncertain=self.new_service(before_finalize=fail)
        with self.assertRaisesRegex(PersistenceFailure,"finalization_uncertain"):
            self.persist(service=uncertain)
        with self.connections() as connection:
            operation,state=connection.execute("SELECT operation_id,state FROM dataset_store.storage_operations").fetchone()
            quota=connection.execute("SELECT used_bytes,reserved_bytes FROM dataset_store.projects WHERE project_id=%s",(self.project_id,)).fetchone()
        self.assertEqual(state,"reconciling"); self.assertEqual(quota[0],0); self.assertGreater(quota[1],0)
        result=self.service.reconcile(operation,trusted_local=True)
        self.assertEqual((result.state,result.action),("ready","published_verified_blobs"))
        self.assertEqual(self.counts()[4],0)

    def test_missing_blob_reconciliation_fails_and_releases_reservation(self):
        def fail(): raise RuntimeError("synthetic")
        uncertain=self.new_service(before_finalize=fail)
        with self.assertRaises(PersistenceFailure): self.persist(service=uncertain)
        with self.connections() as connection:
            operation,reference=connection.execute("SELECT o.operation_id,a.storage_ref FROM dataset_store.storage_operations o JOIN dataset_store.asset_revisions a ON a.revision_id=o.asset_revision_id").fetchone()
        self.store.remove(reference)
        result=self.service.reconcile(operation,trusted_local=True)
        self.assertEqual(result.state,"failed")
        self.assertEqual(self.counts()[3:],(0,0))
        self.assertEqual(self.store.object_count(),0)

    def test_bad_database_and_trust_boundary_fail_without_storage(self):
        with self.assertRaisesRegex(PersistenceFailure,"persistence_disabled"):
            PrivateDatasetPersistenceService(self.connections,self.store).create_project(project_id=uuid.uuid4(),scope_key="x",quota_bytes=1,lifecycle_policy_version="v")
        with self.assertRaisesRegex(PersistenceFailure,"trusted_local_required"):
            self.service.create_project(project_id=uuid.uuid4(),scope_key="x",quota_bytes=1,lifecycle_policy_version="v")
        @contextmanager
        def broken():
            raise RuntimeError("private database detail")
            yield
        service=PrivateDatasetPersistenceService(broken,self.store,settings=DatasetPersistenceSettings(enabled=True))
        with self.assertRaisesRegex(PersistenceFailure,"database_error"):
            service.create_project(project_id=uuid.uuid4(),scope_key="x",quota_bytes=1,lifecycle_policy_version="v",trusted_local=True)
        self.assertEqual(self.store.object_count(),0)

    def test_unvalidated_dataset_and_project_mismatch_are_never_stored(self):
        review_spec=FileSpec(project_id=str(self.project_id),dataset_id="review",filename="synthetic.csv",columns=())
        with self.assertRaisesRegex(PersistenceFailure,"dataset_requires_review"):
            self.service.persist(b"name\nsynthetic\n",review_spec,ParseSettings(enabled=True),
                project_id=self.project_id,asset_key="review",trusted_local=True)
        with self.assertRaisesRegex(PersistenceFailure,"project_scope_mismatch"):
            self.service.persist(b"region,amount\nnorth,10.50\n",self.specification(),ParseSettings(enabled=True),
                project_id=uuid.uuid4(),asset_key="wrong",trusted_local=True)
        self.assertEqual(self.store.object_count(),0)

    def test_ready_result_and_fixed_errors_do_not_expose_paths_or_values(self):
        import contextlib,io
        output=io.StringIO()
        with contextlib.redirect_stdout(output),contextlib.redirect_stderr(output):
            result=self.persist(b"region,amount\nPRIVATE_SYNTHETIC,10.50\n")
        rendered=repr(result)
        self.assertNotIn(str(self.store._root),rendered)
        self.assertNotIn("PRIVATE_SYNTHETIC",rendered)
        self.assertNotIn("private:v1:",rendered)
        self.assertEqual(output.getvalue(),"")


if __name__ == "__main__": unittest.main()
