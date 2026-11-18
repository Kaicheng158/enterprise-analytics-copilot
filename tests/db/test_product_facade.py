"""PV2-04a Product Facade integration tests use disposable PostgreSQL only."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import unittest
import uuid

import psycopg
from psycopg import sql

from backend.auth.models import RegisterRequest
from backend.auth.passwords import Argon2PasswordService
from backend.auth.service import LocalAuthService
from backend.auth.settings import LocalAuthSettings
from backend.auth.store import PostgresAuthStore
from backend.authorization import ProjectAuthorizationService, ProjectAuthorizationSettings
from backend.local_product_facade_app import create_local_product_facade_app
from backend.product_facade.models import ProductFacadeSettings
from backend.product_facade.service import LocalProductFacadeService
from scripts.migrate import apply_migrations, connect_kwargs
from tests.db.test_local_auth import AsgiClient, MutableClock, ORIGIN, PASSWORD


class ProductFacadeDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = connect_kwargs()
        cls.database_name = "eac_test_product_facade_" + uuid.uuid4().hex
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
        self.clock = MutableClock()
        self.auth_settings = LocalAuthSettings(
            enabled=True,
            allowed_origin=ORIGIN,
            rate_limit_secret="synthetic-product-facade-rate-secret-32",
            session_ttl_seconds=300,
        )
        self.facade_settings = ProductFacadeSettings(
            enabled=True,
            allowed_origin=ORIGIN,
            default_project_quota_bytes=1_000_000,
        )
        self.auth = LocalAuthService(
            PostgresAuthStore(self.connections),
            settings=self.auth_settings,
            passwords=Argon2PasswordService(time_cost=1, memory_cost=8_192, parallelism=1),
            clock=self.clock,
        )
        self.authorization = ProjectAuthorizationService(
            self.connections,
            settings=ProjectAuthorizationSettings(enabled=True),
            clock=self.clock,
        )
        self.product = LocalProductFacadeService(
            self.connections,
            self.authorization,
            settings=self.facade_settings,
            clock=self.clock,
        )
        self.app = create_local_product_facade_app(
            product_settings=self.facade_settings,
            auth_settings=self.auth_settings,
            authorization_settings=ProjectAuthorizationSettings(enabled=True),
            auth_service=self.auth,
            authorization_service=self.authorization,
            product_service=self.product,
            connection_factory=self.connections,
        )
        self.client = AsgiClient(self.app)
        self.register(self.client, "owner@example.com")
        self.csrf = self.client.get("/auth-api/session").json()["csrf_token"]

    def register(self, client, account):
        response = client.post(
            "/auth-api/register",
            payload={"account": account, "display_name": "Synthetic User", "password": PASSWORD},
        )
        self.assertEqual(response.status_code, 201)
        return response

    def create_project(self, client=None, csrf=None):
        response = (client or self.client).post(
            "/product-api/projects",
            payload={"contract_version": "create-private-project-v1", "project_kind": "private-analysis"},
            headers={"X-CSRF-Token": csrf or self.csrf},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return uuid.UUID(response.json()["project_id"])

    def seed_ready_revision(self, project_id):
        asset_id, revision_id, dataset_revision_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        now = self.clock.value
        with self.connections() as connection, connection.transaction():
            connection.execute(
                """INSERT INTO dataset_store.assets(project_id,asset_id,asset_key,state)
                   VALUES (%s,%s,'validated_sales','active')""",
                (project_id, asset_id),
            )
            connection.execute(
                """INSERT INTO dataset_store.asset_revisions
                   (project_id,asset_id,revision_id,revision_number,source_sha256,
                    normalized_sha256,parse_contract_sha256,source_format,source_bytes,
                    storage_ref,state,parser_policy_version,lifecycle_policy_version,ready_at)
                   VALUES (%s,%s,%s,1,%s,%s,%s,'csv',128,%s,'ready','tabular-v1',
                           'local-private-v1',%s)""",
                (
                    project_id,
                    asset_id,
                    revision_id,
                    "a" * 64,
                    "b" * 64,
                    "c" * 64,
                    "private:v1:" + "1" * 32 + ":raw",
                    now,
                ),
            )
            connection.execute(
                """INSERT INTO dataset_store.dataset_revisions
                   (project_id,dataset_revision_id,asset_revision_id,normalized_sha256,
                    blob_sha256,dataset_contract_version,parser_policy_version,dataset_bytes,
                    storage_ref,state,ready_at)
                   VALUES (%s,%s,%s,%s,%s,'dataset-v1','tabular-v1',256,%s,'ready',%s)""",
                (
                    project_id,
                    dataset_revision_id,
                    revision_id,
                    "b" * 64,
                    "d" * 64,
                    "private:v1:" + "2" * 32 + ":dataset",
                    now,
                ),
            )
        return asset_id, revision_id, dataset_revision_id

    @staticmethod
    def analysis_payload(ids, *, extra=None, complete=True):
        asset_id, revision_id, dataset_revision_id = ids
        payload = {
            "contract_version": "dataset-analysis-task-v1",
            "question": "Compare revenue by quarter.",
            "datasets": [
                {
                    "asset_id": str(asset_id),
                    "asset_revision_id": str(revision_id),
                    "dataset_revision_id": str(dataset_revision_id),
                }
            ],
            "intent": {
                "operation": "compare",
                "measures": ["revenue"] if complete else [],
                "dimensions": ["quarter"] if complete else [],
                "time_field": None,
                "filters": [],
            },
            "deliverables": {"answer_depth": "short", "chart": True, "report": False},
        }
        if extra:
            payload.update(extra)
        return payload

    def validate(self, project_id, ids, *, client=None, csrf=None, payload=None):
        return (client or self.client).post(
            f"/product-api/projects/{project_id}/analysis-contracts/validate",
            payload=payload or self.analysis_payload(ids),
            headers={"X-CSRF-Token": csrf or self.csrf},
        )

    def test_authenticated_project_workspace_and_contract_validation(self):
        project_id = self.create_project()
        ids = self.seed_ready_revision(project_id)
        workspace = self.client.get("/product-api/workspace")
        self.assertEqual(workspace.status_code, 200)
        self.assertEqual(workspace.json()["projects"][0]["project_id"], str(project_id))
        self.assertFalse(workspace.json()["capabilities"]["dataset_upload"])

        before = self.execution_count()
        validated = self.validate(project_id, ids)
        self.assertEqual(validated.status_code, 200, validated.text)
        body = validated.json()
        self.assertEqual(body["status"], "contract_validated")
        self.assertFalse(body["execution_created"])
        self.assertEqual(body["context"]["project_id"], str(project_id))
        self.assertEqual(body["context"]["datasets"][0]["source_sha256"], "a" * 64)
        self.assertEqual(self.execution_count(), before)

    def test_centralized_clarification_is_truthful_and_creates_no_task(self):
        project_id = self.create_project()
        ids = self.seed_ready_revision(project_id)
        response = self.validate(
            project_id,
            ids,
            payload=self.analysis_payload(ids, complete=False),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "clarification_required")
        self.assertEqual(body["clarification"]["mode"], "centralized")
        self.assertEqual(
            {item["code"] for item in body["clarification"]["questions"]},
            {"measure_required", "comparison_basis_required"},
        )
        self.assertEqual(self.execution_count(), 0)

    def test_cross_user_session_expiry_csrf_and_revocation_fail_closed(self):
        project_id = self.create_project()
        ids = self.seed_ready_revision(project_id)

        other = AsgiClient(self.app)
        self.register(other, "other@example.com")
        other_csrf = other.get("/auth-api/session").json()["csrf_token"]
        self.assertEqual(other.get(f"/product-api/projects/{project_id}").status_code, 404)
        self.assertEqual(self.validate(project_id, ids, client=other, csrf=other_csrf).status_code, 404)
        self.assertEqual(
            self.client.post(
                "/product-api/projects",
                payload={"contract_version": "create-private-project-v1", "project_kind": "private-analysis"},
                headers={"X-CSRF-Token": "wrong"},
            ).status_code,
            403,
        )

        token = self.client.cookie.split("=", 1)[1]
        principal, _ = self.auth.require_principal(token)
        self.authorization.revoke_project(principal, project_id)
        self.assertEqual(self.validate(project_id, ids).status_code, 404)

        self.clock.value += timedelta(seconds=301)
        self.assertEqual(self.client.get("/product-api/workspace").status_code, 401)

    def test_invalid_and_cross_site_requests_are_safe_and_content_free(self):
        project_id = self.create_project()
        ids = self.seed_ready_revision(project_id)
        payload = self.analysis_payload(
            ids,
            extra={"sql": "SELECT secret FROM private_table", "principal": "owner"},
        )
        invalid = self.validate(project_id, ids, payload=payload)
        self.assertEqual(invalid.status_code, 422)
        self.assertNotIn("SELECT secret", invalid.text)
        self.assertNotIn("private_table", invalid.text)

        cross_site = self.client.post(
            f"/product-api/projects/{project_id}/analysis-contracts/validate",
            payload=self.analysis_payload(ids),
            headers={"X-CSRF-Token": self.csrf, "Origin": "https://evil.example"},
        )
        self.assertEqual(cross_site.status_code, 403)

    def test_unready_or_mismatched_revision_is_not_disclosed(self):
        project_id = self.create_project()
        ids = self.seed_ready_revision(project_id)
        wrong = (ids[0], ids[1], uuid.uuid4())
        response = self.validate(project_id, wrong)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "product_resource_unavailable")

    def execution_count(self):
        with self.connections() as connection:
            return connection.execute("SELECT count(*) FROM agent_runtime.executions").fetchone()[0]


if __name__ == "__main__":
    unittest.main()
