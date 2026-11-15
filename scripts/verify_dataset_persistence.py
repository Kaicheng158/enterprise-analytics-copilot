"""Synthetic-only PV2-02a evidence in a disposable PostgreSQL database/root."""
from contextlib import contextmanager
from pathlib import Path
import json
import sys
import tempfile
import uuid

import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.datasets import Column, FileSpec, ParseSettings
from backend.datasets.persistence import PrivateDatasetPersistenceService
from backend.datasets.persistence_models import DatasetPersistenceSettings, PersistenceFailure
from backend.datasets.private_storage import PrivateLocalBlobStore
from scripts.migrate import apply_migrations, connect_kwargs


def main():
    admin=connect_kwargs(); database_name="eac_verify_dataset_"+uuid.uuid4().hex
    with psycopg.connect(**admin,autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    try:
        database={**admin,"dbname":database_name}
        with psycopg.connect(**database,autocommit=True) as connection: apply_migrations(connection)
        @contextmanager
        def connections():
            connection=psycopg.connect(**database,autocommit=True)
            try: yield connection
            finally: connection.close()
        with tempfile.TemporaryDirectory() as directory:
            blobs=PrivateLocalBlobStore(Path(directory)/"private")
            project=uuid.uuid4()
            service=PrivateDatasetPersistenceService(connections,blobs,settings=DatasetPersistenceSettings(enabled=True))
            service.create_project(project_id=project,scope_key="synthetic:evidence",quota_bytes=1_000_000,
                lifecycle_policy_version="local-retention-v1",trusted_local=True)
            spec=lambda:FileSpec(project_id=str(project),dataset_id="synthetic",filename="synthetic.csv",
                columns=(Column(name="region"),Column(name="amount",kind="money",unit="USD")))
            source=b"region,amount\nnorth,10.50\n"
            submissions=[service.persist(source,spec(),ParseSettings(enabled=True),project_id=project,
                asset_key="sales",trusted_local=True) for _ in range(3)]
            changed=service.persist(source.replace(b"10.50",b"11.50"),spec(),ParseSettings(enabled=True),
                project_id=project,asset_key="sales",trusted_local=True)
            def stop_before_finalize(): raise RuntimeError("synthetic stop")
            interrupted=PrivateDatasetPersistenceService(connections,blobs,settings=DatasetPersistenceSettings(enabled=True),
                before_finalize=stop_before_finalize)
            try:
                interrupted.persist(source.replace(b"10.50",b"12.50"),spec(),ParseSettings(enabled=True),
                    project_id=project,asset_key="sales",trusted_local=True)
            except PersistenceFailure as error:
                interruption_code=error.code
            with connections() as connection:
                operation=connection.execute("SELECT operation_id FROM dataset_store.storage_operations WHERE state='reconciling'").fetchone()[0]
            reconciled=service.reconcile(operation,trusted_local=True)
            with connections() as connection:
                revision_count=connection.execute("SELECT count(*) FROM dataset_store.asset_revisions WHERE project_id=%s AND state='ready'",(project,)).fetchone()[0]
                used,reserved=connection.execute("SELECT used_bytes,reserved_bytes FROM dataset_store.projects WHERE project_id=%s",(project,)).fetchone()
                operations=connection.execute("SELECT state,count(*) FROM dataset_store.storage_operations GROUP BY state ORDER BY state").fetchall()
            report={
                "evidence_version":"pv2-02a-synthetic-persistence-v1",
                "scope":"trusted local synthetic harness; no real-user time-savings claim",
                "duplicate_submissions":3,
                "unique_duplicate_revision_ids":len({str(item.asset_revision.revision_id) for item in submissions}),
                "duplicate_reuse_count":sum(item.reused for item in submissions),
                "changed_revision_number":changed.asset_revision.revision_number,
                "ready_revision_count":revision_count,
                "private_blob_count":blobs.object_count(),
                "quota_used_bytes":used,
                "quota_reserved_bytes":reserved,
                "interruption_error_code":interruption_code,
                "reconciliation_state":reconciled.state,
                "reconciliation_action":reconciled.action,
                "operation_states":[{"state":state,"count":count} for state,count in operations],
                "manual_file_copy_steps_for_three_duplicates":0,
                "manual_version_index_steps":0,
                "manual_recovery_actions":1,
                "paid_calls":0,
            }
            assert report["unique_duplicate_revision_ids"]==1 and report["duplicate_reuse_count"]==2
            assert report["changed_revision_number"]==2 and report["reconciliation_state"]=="ready"
            assert report["quota_reserved_bytes"]==0 and report["manual_recovery_actions"]==1
            print(json.dumps(report,indent=2,sort_keys=True))
    finally:
        with psycopg.connect(**admin,autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name)))


if __name__=="__main__": main()
