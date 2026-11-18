"""PV2-04b HTTP and storage integration against disposable PostgreSQL only."""

from __future__ import annotations

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace
import unittest
import uuid

from pydantic import SecretStr
import psycopg
from psycopg import sql

from backend.auth.passwords import Argon2PasswordService
from backend.auth.service import LocalAuthService
from backend.auth.settings import LocalAuthSettings
from backend.auth.store import PostgresAuthStore
from backend.authorization import ProjectAuthorizationService, ProjectAuthorizationSettings
from backend.datasets import DatasetPersistenceSettings, PrivateDatasetPersistenceService
from backend.datasets.private_storage import PrivateLocalBlobStore
from backend.local_product_facade_app import create_local_product_facade_app
from backend.product_facade.datasets import AuthorizedDatasetProductService
from backend.product_facade.models import (
    DatasetUploadMetadata,
    ProductDatasetSettings,
    ProductFacadeFailure,
    ProductFacadeSettings,
)
from backend.product_facade.service import LocalProductFacadeService
from scripts.migrate import apply_migrations, connect_kwargs
from tests.dataset_fixtures import workbook
from tests.db.test_local_auth import AsgiClient, MutableClock, ORIGIN, PASSWORD


class ProductDatasetUploadDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = connect_kwargs()
        cls.database_name = "eac_test_product_upload_" + uuid.uuid4().hex
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
        self.clock = MutableClock()
        self.auth_settings = LocalAuthSettings(
            enabled=True,
            allowed_origin=ORIGIN,
            rate_limit_secret="synthetic-product-upload-rate-secret-32",
            session_ttl_seconds=300,
        )
        self.facade_settings = ProductFacadeSettings(
            enabled=True,
            allowed_origin=ORIGIN,
            default_project_quota_bytes=1_000_000,
        )
        self.dataset_settings = ProductDatasetSettings(
            enabled=True,
            idempotency_secret=SecretStr("synthetic-product-upload-idempotency-secret"),
            storage_root=self.storage.name,
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
        self.persistence = PrivateDatasetPersistenceService(
            self.connections,
            PrivateLocalBlobStore(Path(self.storage.name)),
            settings=DatasetPersistenceSettings(enabled=True),
        )
        self.dataset_service = AuthorizedDatasetProductService(
            self.connections,
            self.authorization,
            self.persistence,
            facade_settings=self.facade_settings,
            dataset_settings=self.dataset_settings,
            clock=self.clock,
        )
        self.app = create_local_product_facade_app(
            product_settings=self.facade_settings,
            dataset_settings=self.dataset_settings,
            auth_settings=self.auth_settings,
            authorization_settings=ProjectAuthorizationSettings(enabled=True),
            auth_service=self.auth,
            authorization_service=self.authorization,
            product_service=LocalProductFacadeService(
                self.connections,
                self.authorization,
                settings=self.facade_settings,
                clock=self.clock,
            ),
            dataset_service=self.dataset_service,
            connection_factory=self.connections,
        )
        self.client = AsgiClient(self.app)
        self.register(self.client, "owner@example.com")
        self.csrf = self.client.get("/auth-api/session").json()["csrf_token"]

    @staticmethod
    def registration(account):
        return {
            "account": account,
            "display_name": "Synthetic Analyst",
            "password": PASSWORD,
        }

    def register(self, client, account):
        response = client.post("/auth-api/register", payload=self.registration(account))
        self.assertEqual(response.status_code, 201)

    def create_project(self, *, client=None, csrf=None, key="project-request-0001", name="Synthetic Project"):
        client = client or self.client
        response = client.post(
            "/product-api/v2/projects",
            payload={"contract_version": "create-private-project-v2", "name": name},
            headers={"X-CSRF-Token": csrf or self.csrf, "Idempotency-Key": key},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    @staticmethod
    def metadata(*, filename="sales.csv", name="Quarterly sales", columns=None):
        payload = {
            "contract_version": "dataset-upload-v1",
            "dataset_name": name,
            "filename": filename,
            "columns": columns
            or [
                {"name": "region", "kind": "text", "required": True},
                {"name": "revenue", "kind": "money", "required": True, "unit": "USD"},
                {"name": "date", "kind": "date", "required": True},
            ],
        }
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    @staticmethod
    def csv(amount="100.25"):
        return f"region,revenue,date\nNorth,{amount},2026-01-31\nSouth,200.50,2026-02-28\n".encode()

    def raw_request(self, client, method, path, body, *, headers):
        async def run():
            output = []
            request_headers = {
                "host": "127.0.0.1:8775",
                "accept": "application/json",
                "origin": ORIGIN,
                "sec-fetch-site": "same-origin",
                "content-length": str(len(body)),
            }
            if client.cookie:
                request_headers["cookie"] = client.cookie
            request_headers.update({key.lower(): value for key, value in headers.items()})
            sent = False

            async def receive():
                nonlocal sent
                if sent:
                    return {"type": "http.disconnect"}
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}

            async def send(message):
                output.append(message)

            await self.app(
                {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "1.1",
                    "method": method,
                    "scheme": "http",
                    "path": path,
                    "raw_path": path.encode(),
                    "query_string": b"",
                    "headers": [(key.encode(), value.encode()) for key, value in request_headers.items()],
                    "server": ("127.0.0.1", 8775),
                    "client": (client.client_host, 12345),
                },
                receive,
                send,
            )
            headers_out = {}
            for key, value in output[0].get("headers", []):
                headers_out.setdefault(key.decode().lower(), []).append(value.decode())
            raw = b"".join(item.get("body", b"") for item in output)
            return SimpleNamespace(
                status_code=output[0]["status"],
                text=raw.decode(),
                headers=headers_out,
                json=lambda: json.loads(raw) if raw else None,
            )

        return asyncio.run(run())

    def upload(self, project_id, *, client=None, csrf=None, key="upload-request-0001", body=None,
               metadata=None, content_type="text/csv"):
        return self.raw_request(
            client or self.client,
            "POST",
            f"/product-api/v2/projects/{project_id}/datasets",
            body or self.csv(),
            headers={
                "content-type": content_type,
                "x-csrf-token": csrf or self.csrf,
                "idempotency-key": key,
                "x-dataset-metadata": metadata or self.metadata(),
            },
        )

    def test_migration_011_is_private_and_history_is_intact(self):
        with self.connections() as connection:
            applied = connection.execute(
                "SELECT array_agg(name ORDER BY name) FROM app_migrations.applied"
            ).fetchone()[0]
            privileges = connection.execute(
                "SELECT has_schema_privilege('public','product_facade','USAGE'),"
                "has_table_privilege('public','product_facade.write_idempotency','SELECT')"
            ).fetchone()
        self.assertEqual(applied[-1], "011_product_dataset_upload.sql")
        self.assertEqual(len(applied), 11)
        self.assertEqual(privileges, (False, False))

    def test_named_project_creation_is_idempotent_and_conflict_safe(self):
        missing_key = self.client.post(
            "/product-api/v2/projects",
            payload={"contract_version": "create-private-project-v2", "name": "No Key"},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(missing_key.status_code, 422)
        legacy_write = self.client.post(
            "/product-api/projects",
            payload={"contract_version": "create-private-project-v1", "project_kind": "private-analysis"},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(legacy_write.status_code, 409)
        first = self.create_project()
        repeated = self.create_project()
        self.assertEqual(first, repeated)
        conflict = self.client.post(
            "/product-api/v2/projects",
            payload={"contract_version": "create-private-project-v2", "name": "Different"},
            headers={"X-CSRF-Token": self.csrf, "Idempotency-Key": "project-request-0001"},
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertNotIn("Synthetic Project", conflict.text)
        workspace = self.client.get("/product-api/v2/workspace").json()
        self.assertEqual([(item["project_id"], item["name"]) for item in workspace["projects"]],
                         [(first["project_id"], "Synthetic Project")])

    def test_csv_upload_list_revision_and_redacted_preview(self):
        project = self.create_project()
        harness = self.client.get("/dataset-upload/")
        self.assertEqual(harness.status_code, 200)
        self.assertIn("LOCAL SYNTHETIC HARNESS", harness.text)
        response = self.upload(project["project_id"])
        self.assertEqual(response.status_code, 201, response.text)
        revision = response.json()
        self.assertFalse(revision["reused"])
        self.assertEqual(revision["quality"]["blocking_errors"], [])
        listing = self.client.get(
            f"/product-api/v2/projects/{project['project_id']}/datasets"
        )
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(len(listing.json()["revisions"]), 1)
        detail = self.client.get(
            f"/product-api/v2/projects/{project['project_id']}/datasets/{revision['dataset_revision_id']}"
        )
        self.assertEqual(detail.status_code, 200)
        preview = self.client.get(
            f"/product-api/v2/projects/{project['project_id']}/datasets/{revision['dataset_revision_id']}/preview"
        )
        self.assertEqual(preview.status_code, 200)
        payload = preview.json()
        self.assertTrue(payload["values_redacted"])
        self.assertEqual([column["name"] for column in payload["tables"][0]["columns"]],
                         ["region", "revenue", "date"])
        self.assertEqual(payload["tables"][0]["rows"][0][1]["value"], "[redacted]")
        for forbidden in ("storage_ref", "private:v1:", self.storage.name, "100.25", "North"):
            self.assertNotIn(forbidden, preview.text)

    def test_same_key_and_duplicate_file_reuse_result_and_quota(self):
        project = self.create_project()
        first = self.upload(project["project_id"]).json()
        used = self.client.get(f"/product-api/v2/projects/{project['project_id']}").json()["used_bytes"]
        repeat = self.upload(project["project_id"]).json()
        duplicate = self.upload(project["project_id"], key="upload-request-0002").json()
        final = self.client.get(f"/product-api/v2/projects/{project['project_id']}").json()
        self.assertEqual(first["dataset_revision_id"], repeat["dataset_revision_id"])
        self.assertEqual(first["dataset_revision_id"], duplicate["dataset_revision_id"])
        self.assertTrue(repeat["reused"])
        self.assertTrue(duplicate["reused"])
        self.assertEqual(final["used_bytes"], used)
        with self.connections() as connection:
            counts = connection.execute(
                "SELECT count(*),count(DISTINCT dataset_revision_id) FROM product_facade.write_idempotency WHERE operation='dataset_upload'"
            ).fetchone()
        self.assertEqual(counts, (2, 1))

    def test_idempotency_conflict_and_concurrent_repeat_create_one_revision(self):
        project = self.create_project()
        first = self.upload(project["project_id"])
        conflict = self.upload(project["project_id"], body=self.csv("999.00"))
        self.assertEqual((first.status_code, conflict.status_code), (201, 409))
        clients = [AsgiClient(self.app), AsgiClient(self.app)]
        for client in clients:
            client.cookie = self.client.cookie
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(
                pool.map(
                    lambda client: self.upload(
                        project["project_id"], client=client, key="upload-request-concurrent"
                    ),
                    clients,
                )
            )
        self.assertEqual([response.status_code for response in responses], [201, 201])
        self.assertEqual(
            len({response.json()["dataset_revision_id"] for response in responses}), 1
        )

    def test_csv_xlsx_normalize_equivalently_and_chinese_metadata_round_trips(self):
        project = self.create_project(name="中文项目")
        columns = [
            {"name": "地区", "kind": "text", "required": True},
            {"name": "金额", "kind": "money", "required": True, "unit": "USD"},
            {"name": "日期", "kind": "date", "required": True},
        ]
        csv_body = "地区,金额,日期\n华东,123.40,2026-01-31\n".encode()
        csv_result = self.upload(
            project["project_id"],
            key="upload-chinese-csv-0001",
            body=csv_body,
            metadata=self.metadata(filename="销售.csv", name="销售数据", columns=columns),
        ).json()
        xlsx_body = workbook([["地区", "金额", "日期"], ["华东", "123.40", "2026-01-31"]])
        xlsx_result = self.upload(
            project["project_id"],
            key="upload-chinese-xlsx-0001",
            body=xlsx_body,
            metadata=self.metadata(filename="销售.xlsx", name="销售数据", columns=columns),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ).json()
        self.assertEqual(csv_result["normalized_sha256"], xlsx_result["normalized_sha256"])
        self.assertNotEqual(csv_result["source_sha256"], xlsx_result["source_sha256"])
        self.assertEqual(csv_result["dataset_name"], "销售数据")
        self.assertEqual(csv_result["original_filename"], "销售.csv")
        self.assertEqual(xlsx_result["original_filename"], "销售.xlsx")
        listing = self.client.get(
            f"/product-api/v2/projects/{project['project_id']}/datasets"
        ).json()["revisions"]
        self.assertEqual(
            {item["source_format"]: item["original_filename"] for item in listing},
            {"csv": "销售.csv", "xlsx": "销售.xlsx"},
        )

    def test_quality_issues_are_grouped_and_not_persisted(self):
        project = self.create_project()
        formula = self.upload(
            project["project_id"],
            body=b"region,revenue,date\nNorth,=1+1,01/02/2026\n",
        )
        self.assertEqual(formula.status_code, 422)
        quality = formula.json()["detail"]["quality"]
        self.assertEqual(
            {item["code"] for item in quality["confirmations"]}, {"date_ambiguous"}
        )
        self.assertEqual(
            {item["code"] for item in quality["blocking_errors"]}, {"formula_like_text"}
        )
        self.assertNotIn("=1+1", formula.text)
        listing = self.client.get(
            f"/product-api/v2/projects/{project['project_id']}/datasets"
        ).json()
        self.assertEqual(listing["revisions"], [])

    def test_file_type_mime_size_quota_and_contract_fail_closed(self):
        project = self.create_project()
        mismatch = self.upload(project["project_id"], body=b"PKbad")
        self.assertEqual(mismatch.status_code, 422)
        bad_mime = self.upload(project["project_id"], content_type="text/html")
        self.assertEqual(bad_mime.status_code, 422)
        oversized = self.raw_request(
            self.client,
            "POST",
            f"/product-api/v2/projects/{project['project_id']}/datasets",
            b"x",
            headers={
                "content-type": "text/csv",
                "content-length": str(self.dataset_settings.max_file_bytes + 1),
                "x-csrf-token": self.csrf,
                "idempotency-key": "upload-oversized-0001",
                "x-dataset-metadata": self.metadata(),
            },
        )
        self.assertEqual(oversized.status_code, 413)
        with self.connections() as connection, connection.transaction():
            connection.execute(
                "UPDATE dataset_store.projects SET quota_bytes=100 WHERE project_id=%s",
                (project["project_id"],),
            )
        quota = self.upload(project["project_id"], key="upload-quota-test-0001")
        self.assertEqual(quota.status_code, 429)
        bad_metadata = base64.urlsafe_b64encode(
            json.dumps({
                "contract_version": "dataset-upload-v1",
                "dataset_name": "Sales",
                "filename": "sales.csv",
                "columns": [{"name": "region"}],
                "trusted_local": True,
            }).encode()
        ).rstrip(b"=").decode()
        rejected = self.upload(
            project["project_id"], key="upload-authority-test-0001", metadata=bad_metadata
        )
        self.assertEqual(rejected.status_code, 422)

    def test_cross_user_cross_project_expiry_and_revocation_are_hidden(self):
        project = self.create_project()
        revision = self.upload(project["project_id"]).json()
        other = AsgiClient(self.app)
        self.register(other, "other@example.com")
        other_csrf = other.get("/auth-api/session").json()["csrf_token"]
        self.assertEqual(
            other.get(f"/product-api/v2/projects/{project['project_id']}/datasets").status_code,
            404,
        )
        other_project = self.create_project(
            client=other, csrf=other_csrf, key="other-project-request-0001", name="Other"
        )
        self.assertEqual(
            other.get(
                f"/product-api/v2/projects/{other_project['project_id']}/datasets/{revision['dataset_revision_id']}"
            ).status_code,
            404,
        )
        with self.connections() as connection, connection.transaction():
            connection.execute(
                "UPDATE dataset_store.assets SET state='expired' WHERE project_id=%s AND asset_id=%s",
                (project["project_id"], revision["asset_id"]),
            )
        self.assertEqual(
            self.client.get(
                f"/product-api/v2/projects/{project['project_id']}/datasets/{revision['dataset_revision_id']}"
            ).status_code,
            404,
        )
        with self.connections() as connection, connection.transaction():
            connection.execute(
                "UPDATE dataset_store.assets SET state='active' WHERE project_id=%s AND asset_id=%s",
                (project["project_id"], revision["asset_id"]),
            )
        principal, _ = self.auth.require_principal(self.client.cookie.split("=", 1)[1])
        self.authorization.revoke_project(principal, uuid.UUID(project["project_id"]))
        self.assertEqual(
            self.client.get(f"/product-api/v2/projects/{project['project_id']}/datasets").status_code,
            404,
        )

    def test_session_expiry_csrf_and_origin_are_enforced_before_upload(self):
        project = self.create_project()
        no_csrf = self.upload(project["project_id"], csrf="invalid")
        self.assertEqual(no_csrf.status_code, 403)
        cross_site = self.raw_request(
            self.client,
            "POST",
            f"/product-api/v2/projects/{project['project_id']}/datasets",
            self.csv(),
            headers={
                "content-type": "text/csv",
                "origin": "http://attacker.invalid",
                "sec-fetch-site": "cross-site",
                "x-csrf-token": self.csrf,
                "idempotency-key": "upload-cross-site-0001",
                "x-dataset-metadata": self.metadata(),
            },
        )
        self.assertEqual(cross_site.status_code, 403)
        self.clock.value += timedelta(seconds=301)
        expired = self.upload(project["project_id"], key="upload-expired-session-0001")
        self.assertEqual(expired.status_code, 401)
        with self.connections() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM dataset_store.asset_revisions WHERE project_id=%s",
                    (project["project_id"],),
                ).fetchone()[0],
                0,
            )

    def test_write_fence_serializes_revocation_without_weakening_authorization(self):
        project = self.create_project()
        token = self.client.cookie.split("=", 1)[1]
        principal, _ = self.auth.require_principal(token)
        started = threading.Event()
        finished = threading.Event()

        def revoke():
            started.set()
            self.authorization.revoke_project(principal, uuid.UUID(project["project_id"]))
            finished.set()

        with self.authorization.project_write_fence(principal, uuid.UUID(project["project_id"])):
            thread = threading.Thread(target=revoke)
            thread.start()
            self.assertTrue(started.wait(1))
            time.sleep(0.05)
            self.assertFalse(finished.is_set())
        thread.join(2)
        self.assertTrue(finished.is_set())
        self.assertEqual(
            self.client.get(f"/product-api/v2/projects/{project['project_id']}").status_code,
            404,
        )

    def test_interrupted_blob_write_is_not_visible_and_retry_recovers_idempotently(self):
        project = self.create_project()
        token = self.client.cookie.split("=", 1)[1]
        principal, _ = self.auth.require_principal(token)
        metadata_raw = base64.urlsafe_b64decode(self.metadata() + "==")
        metadata = DatasetUploadMetadata.model_validate_json(metadata_raw)

        def crash_after_reserve():
            raise RuntimeError("synthetic process interruption")

        crashing_persistence = PrivateDatasetPersistenceService(
            self.connections,
            PrivateLocalBlobStore(Path(self.storage.name)),
            settings=DatasetPersistenceSettings(enabled=True),
            after_reserve=crash_after_reserve,
        )
        crashing_service = AuthorizedDatasetProductService(
            self.connections,
            self.authorization,
            crashing_persistence,
            facade_settings=self.facade_settings,
            dataset_settings=self.dataset_settings,
            clock=self.clock,
        )
        with self.assertRaises(ProductFacadeFailure) as stopped:
            crashing_service.upload(
                principal,
                uuid.UUID(project["project_id"]),
                metadata,
                self.csv(),
                idempotency_key="upload-crash-recovery-0001",
            )
        self.assertEqual(stopped.exception.code, "product_storage_unavailable")
        with self.connections() as connection:
            operation = connection.execute(
                """SELECT operation_id,state FROM dataset_store.storage_operations
                   WHERE project_id=%s""",
                (project["project_id"],),
            ).fetchone()
            ready_count = connection.execute(
                "SELECT count(*) FROM dataset_store.asset_revisions WHERE project_id=%s AND state='ready'",
                (project["project_id"],),
            ).fetchone()[0]
            idem_count = connection.execute(
                "SELECT count(*) FROM product_facade.write_idempotency WHERE operation='dataset_upload'"
            ).fetchone()[0]
        self.assertEqual((operation[1], ready_count, idem_count), ("writing", 0, 0))
        reconciled = self.persistence.reconcile(operation[0], trusted_local=True)
        self.assertEqual(reconciled.state, "failed")
        recovered = self.dataset_service.upload(
            principal,
            uuid.UUID(project["project_id"]),
            metadata,
            self.csv(),
            idempotency_key="upload-crash-recovery-0001",
        )
        self.assertEqual(recovered.state, "ready")
        with self.connections() as connection:
            states = connection.execute(
                "SELECT state FROM dataset_store.asset_revisions WHERE project_id=%s",
                (project["project_id"],),
            ).fetchall()
        self.assertEqual(states, [("ready",)])


if __name__ == "__main__":
    unittest.main()
