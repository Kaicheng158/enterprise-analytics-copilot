"""PV2-04c authorization and persistence integration in disposable PostgreSQL."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import uuid

from pydantic import SecretStr
import psycopg
from psycopg import sql

from backend.auth.models import RegisterRequest
from backend.auth.passwords import Argon2PasswordService
from backend.auth.service import LocalAuthService
from backend.auth.settings import LocalAuthSettings
from backend.auth.store import PostgresAuthStore
from backend.authorization import ProjectAuthorizationService, ProjectAuthorizationSettings
from backend.datasets import DatasetPersistenceSettings, PrivateDatasetPersistenceService
from backend.datasets.analysis_models import DatasetAnalysisSettings
from backend.datasets.private_storage import PrivateLocalBlobStore
from backend.product_facade.datasets import AuthorizedDatasetProductService
from backend.product_facade.models import (
    CreatePrivateProjectRequestV2,
    DatasetRevisionBinding,
    DatasetUploadMetadata,
    ProductDatasetSettings,
    ProductFacadeSettings,
)
from backend.tools.dataset_analysis import (
    AuthorizedDatasetAnalysisRuntime,
    DATASET_ANALYSIS_PRINCIPAL,
    build_dataset_analysis_executor,
)
from backend.tools.models import ToolCall, ToolExecutionContext
from scripts.migrate import apply_migrations, connect_kwargs
from tests.dataset_fixtures import workbook


PASSWORD = SecretStr("Synthetic-Only-Analysis-Password!")


class DatasetAnalysisDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = connect_kwargs()
        cls.database_name = "eac_test_dataset_analysis_" + uuid.uuid4().hex
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

    @contextmanager
    def connections(self):
        connection = psycopg.connect(**self.database)
        try:
            yield connection
        finally:
            connection.close()

    def setUp(self):
        with psycopg.connect(**self.database, autocommit=True) as connection:
            connection.execute("TRUNCATE product_auth.users CASCADE")
            connection.execute("TRUNCATE dataset_store.projects CASCADE")
            connection.execute("TRUNCATE agent_runtime.executions CASCADE")
        self.storage = TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        self.auth = LocalAuthService(
            PostgresAuthStore(self.connections),
            settings=LocalAuthSettings(
                enabled=True,
                allowed_origin="http://127.0.0.1:8775",
                rate_limit_secret=SecretStr("synthetic-analysis-rate-secret-0001"),
            ),
            passwords=Argon2PasswordService(time_cost=1, memory_cost=8_192, parallelism=1),
        )
        self.authorization = ProjectAuthorizationService(
            self.connections, settings=ProjectAuthorizationSettings(enabled=True)
        )
        self.blobs = PrivateLocalBlobStore(Path(self.storage.name))
        self.persistence = PrivateDatasetPersistenceService(
            self.connections,
            self.blobs,
            settings=DatasetPersistenceSettings(enabled=True),
        )
        self.facade_settings = ProductFacadeSettings(enabled=True)
        self.dataset_settings = ProductDatasetSettings(
            enabled=True,
            idempotency_secret=SecretStr("synthetic-analysis-idempotency-secret-0001"),
            storage_root=self.storage.name,
        )
        self.products = AuthorizedDatasetProductService(
            self.connections,
            self.authorization,
            self.persistence,
            facade_settings=self.facade_settings,
            dataset_settings=self.dataset_settings,
        )
        self.owner = self.register("owner@example.test")

    def register(self, account):
        _view, token = self.auth.register(
            RegisterRequest(account=account, display_name="Synthetic Analyst", password=PASSWORD),
            client_address="127.0.0.1",
        )
        return self.auth.require_principal(token)[0]

    def create_project(self, principal=None, key=None, name="Analysis Project"):
        principal = principal or self.owner
        return self.products.create_project(
            principal,
            CreatePrivateProjectRequestV2(name=name),
            idempotency_key=key or "analysis-project-request-0001",
        )

    @staticmethod
    def metadata(filename, name, columns):
        return DatasetUploadMetadata(
            dataset_name=name, filename=filename, columns=tuple(columns)
        )

    def upload(self, project_id, *, principal=None, key, source, metadata):
        return self.products.upload(
            principal or self.owner,
            project_id,
            metadata,
            source,
            idempotency_key=key,
        )

    @staticmethod
    def binding(revision):
        return DatasetRevisionBinding(
            asset_id=revision.asset_id,
            asset_revision_id=revision.asset_revision_id,
            dataset_revision_id=revision.dataset_revision_id,
        )

    def executor(self, principal, project_id, *, settings=None, authorization=None):
        runtime = AuthorizedDatasetAnalysisRuntime(
            self.connections,
            authorization or self.authorization,
            self.persistence,
            principal=principal,
            project_id=project_id,
            settings=settings or DatasetAnalysisSettings(enabled=True),
        )
        return build_dataset_analysis_executor(runtime)

    @staticmethod
    def execute(executor, name, arguments):
        return executor.execute(
            ToolCall(name=name, arguments=arguments),
            ToolExecutionContext(
                principal=DATASET_ANALYSIS_PRINCIPAL,
                correlation_id="pv204c-database-test",
            ),
        )

    def finance_dataset(self, project_id, *, key="analysis-upload-finance-0001"):
        columns = [
            {"name": "region", "kind": "text", "required": True},
            {"name": "period", "kind": "date", "required": True},
            {"name": "revenue", "kind": "money", "required": True, "unit": "USD"},
            {"name": "cost", "kind": "money", "required": True, "unit": "USD"},
        ]
        source = (
            b"region,period,revenue,cost\n"
            b"North,2026-01-31,100.25,60.00\n"
            b"South,2026-01-31,-20.25,30.00\n"
            b"North,2026-02-28,120.00,80.00\n"
            b"South,2026-02-28,0,0\n"
        )
        return self.upload(
            project_id,
            key=key,
            source=source,
            metadata=self.metadata("finance.csv", "Finance", columns),
        )

    def test_authorized_registry_reads_exact_revision_and_is_repeatable(self):
        project = self.create_project()
        revision = self.finance_dataset(project.project_id)
        arguments = {
            "binding": self.binding(revision).model_dump(mode="json"),
            "filters": [{"field": "period", "operator": "between", "values": ["2026-01-01", "2026-12-31"]}],
            "group_by": ["region"],
            "aggregations": [{"result_id": "sales", "function": "sum", "field": "revenue"}],
            "sort": [{"key": "sales", "direction": "descending"}],
        }
        executor = self.executor(self.owner, project.project_id)
        first = self.execute(executor, "aggregate_dataset_revision", arguments)
        second = self.execute(executor, "aggregate_dataset_revision", arguments)
        self.assertEqual((first.status, second.status), ("success", "success"))
        self.assertEqual(first.output, second.output)
        self.assertEqual(
            [(row["dimensions"][0]["result"]["value"], row["measures"][0]["result"]["value"]) for row in first.output["rows"]],
            [("North", "220.25"), ("South", "-20.25")],
        )
        self.assertEqual(first.output["dataset_revision_id"], str(revision.dataset_revision_id))
        self.assertNotIn(self.storage.name, str(first.output))

    def test_multiple_csv_xlsx_structures_profile_rank_and_derived(self):
        project = self.create_project()
        finance = self.finance_dataset(project.project_id)
        people_columns = [
            {"name": "team", "kind": "text", "required": True},
            {"name": "headcount", "kind": "decimal", "required": True},
            {"name": "capacity", "kind": "decimal", "required": True},
        ]
        people = self.upload(
            project.project_id,
            key="analysis-upload-people-0001",
            source=workbook([
                ["team", "headcount", "capacity"],
                ["A", "10", "8"],
                ["B", "20", "16"],
                ["C", "5", "5"],
            ]),
            metadata=self.metadata("people.xlsx", "People", people_columns),
        )
        executor = self.executor(self.owner, project.project_id)
        profile = self.execute(executor, "profile_dataset_revision", {
            "binding": self.binding(finance).model_dump(mode="json"), "fields": ["revenue", "cost"]
        })
        ranking = self.execute(executor, "rank_dataset_revision", {
            "binding": self.binding(people).model_dump(mode="json"),
            "dimension": "team", "measure": "headcount", "aggregation": "sum", "direction": "highest", "limit": 2,
        })
        derived = self.execute(executor, "derive_dataset_metric", {
            "binding": self.binding(people).model_dump(mode="json"),
            "numerator_field": "capacity", "denominator_field": "headcount",
            "aggregation": "sum", "operation": "percentage", "result_id": "utilization",
        })
        self.assertEqual(profile.status, "success")
        self.assertEqual([row["dimensions"][0]["result"]["value"] for row in ranking.output["rows"]], ["B", "A"])
        self.assertEqual(derived.output["rows"][0]["measures"][2]["result"]["value"], "82.8571")
        self.assertEqual(people.source_format, "xlsx")

    def test_cross_user_cross_project_and_forged_binding_fail_closed(self):
        project = self.create_project()
        revision = self.finance_dataset(project.project_id)
        other = self.register("other@example.test")
        other_project = self.create_project(other, "other-analysis-project-0001", "Other")
        arguments = {"binding": self.binding(revision).model_dump(mode="json"), "fields": ["revenue"]}
        cross_user = self.execute(self.executor(other, project.project_id), "profile_dataset_revision", arguments)
        cross_project = self.execute(self.executor(self.owner, other_project.project_id), "profile_dataset_revision", arguments)
        forged = dict(arguments)
        forged["binding"] = {**arguments["binding"], "asset_id": str(uuid.uuid4())}
        forged_result = self.execute(self.executor(self.owner, project.project_id), "profile_dataset_revision", forged)
        self.assertEqual(cross_user.error.code, "tool_not_authorized")
        self.assertEqual(cross_project.error.code, "tool_not_authorized")
        self.assertEqual(forged_result.error.code, "tool_not_authorized")

    def test_revoked_expired_and_tampered_revisions_do_not_publish(self):
        project = self.create_project()
        revision = self.finance_dataset(project.project_id)
        arguments = {"binding": self.binding(revision).model_dump(mode="json"), "fields": ["revenue"]}
        executor = self.executor(self.owner, project.project_id)
        self.authorization.revoke_project(self.owner, project.project_id)
        revoked = self.execute(executor, "profile_dataset_revision", arguments)
        self.assertEqual(revoked.error.code, "tool_not_authorized")

        project2 = self.create_project(key="analysis-project-request-0002", name="Expired")
        revision2 = self.finance_dataset(project2.project_id, key="analysis-upload-finance-0002")
        with self.connections() as connection, connection.transaction():
            connection.execute(
                "UPDATE dataset_store.assets SET state='expired' WHERE project_id=%s AND asset_id=%s",
                (project2.project_id, revision2.asset_id),
            )
        expired = self.execute(self.executor(self.owner, project2.project_id), "profile_dataset_revision", {
            "binding": self.binding(revision2).model_dump(mode="json"), "fields": ["revenue"]
        })
        self.assertEqual(expired.error.code, "tool_not_authorized")

        project3 = self.create_project(key="analysis-project-request-0003", name="Tampered")
        revision3 = self.finance_dataset(project3.project_id, key="analysis-upload-finance-0003")
        with self.connections() as connection:
            reference = connection.execute(
                "SELECT storage_ref FROM dataset_store.dataset_revisions WHERE project_id=%s AND dataset_revision_id=%s",
                (project3.project_id, revision3.dataset_revision_id),
            ).fetchone()[0]
        self.blobs._path(reference).write_bytes(b"{}")
        tampered = self.execute(self.executor(self.owner, project3.project_id), "profile_dataset_revision", {
            "binding": self.binding(revision3).model_dump(mode="json"), "fields": ["revenue"]
        })
        self.assertEqual(tampered.error.code, "tool_execution_failed")

    def test_expired_session_cannot_read_or_publish(self):
        project = self.create_project()
        revision = self.finance_dataset(project.project_id)
        with self.connections() as connection, connection.transaction():
            connection.execute(
                "UPDATE product_auth.sessions SET state='expired', expires_at=created_at+interval '1 second' WHERE session_id=%s",
                (self.owner.session_id,),
            )
        result = self.execute(
            self.executor(self.owner, project.project_id),
            "profile_dataset_revision",
            {"binding": self.binding(revision).model_dump(mode="json"), "fields": ["revenue"]},
        )
        self.assertEqual(result.error.code, "tool_not_authorized")

    def test_authorization_revoked_between_read_and_publish_is_rejected(self):
        project = self.create_project()
        revision = self.finance_dataset(project.project_id)
        service = self.authorization

        class RevokeOnSecondRead:
            def __init__(self):
                self.calls = 0

            def require_asset(inner, principal, project_id, asset_id, *, action):
                inner.calls += 1
                if inner.calls == 2:
                    service.revoke_project(principal, project_id)
                return service.require_asset(principal, project_id, asset_id, action=action)

        result = self.execute(
            self.executor(self.owner, project.project_id, authorization=RevokeOnSecondRead()),
            "profile_dataset_revision",
            {"binding": self.binding(revision).model_dump(mode="json"), "fields": ["revenue"]},
        )
        self.assertEqual(result.error.code, "tool_not_authorized")

    def test_revision_expired_between_read_and_publish_is_rejected(self):
        project = self.create_project()
        revision = self.finance_dataset(project.project_id)
        service = self.authorization

        class ExpireOnSecondRead:
            def __init__(inner):
                inner.calls = 0

            def require_asset(inner, principal, project_id, asset_id, *, action):
                inner.calls += 1
                if inner.calls == 2:
                    with self.connections() as connection, connection.transaction():
                        connection.execute(
                            "UPDATE dataset_store.asset_revisions SET state='reconciling', storage_ref=NULL, ready_at=NULL WHERE project_id=%s AND revision_id=%s",
                            (project_id, revision.asset_revision_id),
                        )
                        connection.execute(
                            "UPDATE dataset_store.dataset_revisions SET state='reconciling', storage_ref=NULL, ready_at=NULL WHERE project_id=%s AND dataset_revision_id=%s",
                            (project_id, revision.dataset_revision_id),
                        )
                return service.require_asset(principal, project_id, asset_id, action=action)

        result = self.execute(
            self.executor(self.owner, project.project_id, authorization=ExpireOnSecondRead()),
            "profile_dataset_revision",
            {"binding": self.binding(revision).model_dump(mode="json"), "fields": ["revenue"]},
        )
        self.assertEqual(result.error.code, "tool_not_authorized")

    def test_resource_limit_invalid_fields_zero_denominator_and_no_match(self):
        project = self.create_project()
        revision = self.finance_dataset(project.project_id)
        binding = self.binding(revision).model_dump(mode="json")
        limited = self.execute(
            self.executor(self.owner, project.project_id, settings=DatasetAnalysisSettings(enabled=True, max_rows=2)),
            "profile_dataset_revision",
            {"binding": binding, "fields": ["revenue"]},
        )
        self.assertEqual(limited.error.code, "tool_execution_failed")
        executor = self.executor(self.owner, project.project_id)
        invalid = self.execute(executor, "aggregate_dataset_revision", {
            "binding": binding,
            "aggregations": [{"result_id": "value", "function": "sum", "field": "missing"}],
        })
        no_match = self.execute(executor, "aggregate_dataset_revision", {
            "binding": binding,
            "filters": [{"field": "region", "operator": "eq", "values": ["East"]}],
            "aggregations": [{"result_id": "rows", "function": "count_rows"}],
        })
        zero = self.execute(executor, "compare_dataset_revision", {
            "binding": binding,
            "filters": [{"field": "region", "operator": "eq", "values": ["South"]}],
            "dimension": "period", "measure": "cost", "aggregation": "sum",
            "baseline": "2026-02-28", "comparison": "2026-01-31",
            "derived_metric": "percentage_change",
        })
        self.assertEqual(invalid.output["status"], "needs_clarification")
        self.assertEqual(no_match.output["findings"][0]["code"], "no_matching_rows")
        self.assertIsNone(zero.output["rows"][0]["measures"][2]["result"]["value"])


if __name__ == "__main__":
    unittest.main()
