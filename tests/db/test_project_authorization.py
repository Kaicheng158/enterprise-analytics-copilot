"""PV2-03b uses one disposable PostgreSQL database and synthetic identities."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import unittest
import uuid

import psycopg
from psycopg import sql

from backend.auth.passwords import Argon2PasswordService
from backend.auth.service import LocalAuthService
from backend.auth.settings import LocalAuthSettings
from backend.auth.store import PostgresAuthStore
from backend.authorization import (
    AuthorizationFailure,
    ProjectAuthorizationService,
    ProjectAuthorizationSettings,
    project_scope_key,
)
from backend.planning.checkpoint_recovery import initial_agent_state
from backend.planning.checkpoint_store import LeaseUnavailable, PostgresCheckpointStore
from backend.planning.demo import build_quarter_sales_comparison_plan
from backend.planning.models import PlanningRequest
from scripts.migrate import apply_migrations, connect_kwargs


NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


class ProjectAuthorizationDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = connect_kwargs()
        cls.database_name = "eac_test_project_authz_" + uuid.uuid4().hex
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
        auth_settings = LocalAuthSettings(
            enabled=True,
            allowed_origin="http://127.0.0.1:8775",
            rate_limit_secret="synthetic-project-auth-rate-secret-32",
        )
        self.auth = LocalAuthService(
            PostgresAuthStore(self.connections),
            settings=auth_settings,
            passwords=Argon2PasswordService(time_cost=1, memory_cost=8_192, parallelism=1),
            clock=lambda: NOW,
        )
        self.authorization = ProjectAuthorizationService(
            self.connections,
            settings=ProjectAuthorizationSettings(enabled=True),
            clock=lambda: NOW,
        )
        self.checkpoints = PostgresCheckpointStore(
            self.connections,
            schema="agent_runtime",
            idempotency_secret=b"project-authorization-test-secret-32-bytes",
            project_authorization_enabled=True,
        )
        self.owner, self.owner_token = self.create_principal("owner@example.com")
        self.other, self.other_token = self.create_principal("other@example.com")
        self.project_id = uuid.uuid4()
        self.authorization.create_owned_project(
            self.owner,
            project_id=self.project_id,
            quota_bytes=1_000_000,
            lifecycle_policy_version="local-private-v1",
        )

    def create_principal(self, account):
        from backend.auth.models import RegisterRequest

        _, token = self.auth.register(
            RegisterRequest(
                account=account,
                display_name=account.split("@", 1)[0],
                password="synthetic-password-123",
            ),
            client_address="127.0.0.1",
        )
        principal, _ = self.auth.require_principal(token)
        return principal, token

    def create_execution(self, project_id=None, key="project-task-001"):
        project_id = project_id or self.project_id
        plan = build_quarter_sales_comparison_plan(PlanningRequest())
        return self.checkpoints.create_or_get_execution(
            scope_key=project_scope_key(project_id),
            idempotency_key=key,
            request_sha256="a" * 64,
            plan=plan,
            initial_state=initial_agent_state(plan),
            policy_version="bounded-plan-policy-v1",
            runtime_version="durable-controlled-planner-v1",
            now=NOW,
        ).execution

    def test_migration_is_private_and_versioned(self):
        with self.connections() as connection:
            applied = connection.execute(
                "SELECT count(*) FROM app_migrations.applied WHERE name='010_project_authorization.sql'"
            ).fetchone()[0]
            tables = connection.execute(
                """SELECT count(*) FROM information_schema.tables
                   WHERE table_schema='product_auth'
                     AND table_name IN ('project_owners','project_task_bindings','authorization_audit_events')"""
            ).fetchone()[0]
            privileges = connection.execute(
                "SELECT has_table_privilege('public','product_auth.project_owners','SELECT'),"
                "has_table_privilege('public','product_auth.project_task_bindings','SELECT')"
            ).fetchone()
        self.assertEqual((applied, tables, privileges), (1, 3, (False, False)))

    def test_project_and_asset_paths_default_deny_cross_user(self):
        own = self.authorization.require_project(self.owner, self.project_id, action="project_read")
        self.assertEqual(own.user_id, self.owner.user_id)
        with self.assertRaisesRegex(AuthorizationFailure, "authorization_denied"):
            self.authorization.require_project(self.other, self.project_id, action="project_read")

        asset_id = uuid.uuid4()
        with self.connections() as connection, connection.transaction():
            connection.execute(
                """INSERT INTO dataset_store.assets(project_id,asset_id,asset_key,state)
                   VALUES (%s,%s,'synthetic_sales','active')""",
                (self.project_id, asset_id),
            )
        asset = self.authorization.require_asset(
            self.owner, self.project_id, asset_id, action="asset_read"
        )
        self.assertEqual(asset.asset_id, asset_id)
        with self.assertRaises(AuthorizationFailure):
            self.authorization.require_asset(
                self.other, self.project_id, asset_id, action="asset_read"
            )
        with self.assertRaises(AuthorizationFailure):
            self.authorization.require_asset(
                self.owner, self.project_id, uuid.uuid4(), action="asset_read"
            )

    def test_task_binding_rejects_wrong_scope_and_cross_user(self):
        execution = self.create_execution()
        binding = self.authorization.bind_task(self.owner, self.project_id, execution.execution_id)
        self.assertEqual(binding.owner_user_id, self.owner.user_id)
        self.assertEqual(
            self.authorization.require_task(
                self.owner, self.project_id, execution.execution_id
            ).execution_id,
            execution.execution_id,
        )
        with self.assertRaises(AuthorizationFailure):
            self.authorization.require_task(
                self.other, self.project_id, execution.execution_id
            )

        foreign_project = uuid.uuid4()
        self.authorization.create_owned_project(
            self.owner,
            project_id=foreign_project,
            quota_bytes=1_000_000,
            lifecycle_policy_version="local-private-v1",
        )
        wrong_scope = self.create_execution(
            project_id=foreign_project,
            key="wrong-project-task",
        )
        with self.assertRaises(AuthorizationFailure):
            self.authorization.bind_task(self.owner, self.project_id, wrong_scope.execution_id)

    def test_revocation_propagates_to_reads_worker_claim_and_checkpoint_writes(self):
        execution = self.create_execution(key="revocation-task")
        self.authorization.bind_task(self.owner, self.project_id, execution.execution_id)
        leased = self.checkpoints.acquire_lease(
            execution.execution_id, worker_id="owner-worker", now=NOW, ttl_seconds=30
        )
        revoked = self.authorization.revoke_project(self.owner, self.project_id)
        self.assertEqual((revoked.state, revoked.authorization_version), ("revoked", 2))

        for check in (
            lambda: self.authorization.require_project(
                self.owner, self.project_id, action="project_read"
            ),
            lambda: self.authorization.require_task(
                self.owner, self.project_id, execution.execution_id
            ),
            lambda: self.authorization.require_task_execution(execution.execution_id),
        ):
            with self.assertRaises(AuthorizationFailure):
                check()
        with self.assertRaises(LeaseUnavailable):
            self.checkpoints.renew_lease(
                execution.execution_id,
                worker_id="owner-worker",
                expected_state_version=leased.state_version,
                now=NOW,
                ttl_seconds=30,
            )
        self.assertIsNone(
            self.checkpoints.claim_next_execution(
                scope_key=project_scope_key(self.project_id),
                worker_id="takeover-worker",
                now=NOW,
                ttl_seconds=30,
            )
        )
        with self.connections() as connection:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with connection.transaction():
                    connection.execute(
                        """INSERT INTO agent_runtime.checkpoints
                           (execution_id,sequence,state_version,execution_status,
                            agent_state,state_sha256,created_at)
                           SELECT execution_id,1,1,'running',plan_json,%s,%s
                           FROM agent_runtime.executions WHERE execution_id=%s""",
                        ("b" * 64, NOW, execution.execution_id),
                    )

    def test_revoked_session_cannot_authorize_and_audit_is_content_free(self):
        self.auth.logout(self.owner_token, self.auth.current(self.owner_token).csrf_token)
        with self.assertRaises(AuthorizationFailure):
            self.authorization.require_project(self.owner, self.project_id, action="project_read")
        with self.connections() as connection:
            decisions = connection.execute(
                """SELECT decision,reason_code FROM product_auth.authorization_audit_events
                   WHERE project_id=%s ORDER BY created_at,event_id""",
                (self.project_id,),
            ).fetchall()
            columns = connection.execute(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_schema='product_auth' AND table_name='authorization_audit_events'"""
            ).fetchall()
        self.assertIn(("denied", "authorization_denied"), decisions)
        self.assertFalse({"account", "display_name", "password", "token"} & {row[0] for row in columns})

    def test_disabled_owner_cannot_read_or_progress_bound_task(self):
        execution = self.create_execution(key="disabled-owner-task")
        self.authorization.bind_task(self.owner, self.project_id, execution.execution_id)
        with self.connections() as connection, connection.transaction():
            connection.execute(
                "UPDATE product_auth.users SET state='disabled',updated_at=%s WHERE user_id=%s",
                (NOW, self.owner.user_id),
            )
        with self.assertRaises(AuthorizationFailure):
            self.authorization.require_project(self.owner, self.project_id, action="project_read")
        with self.assertRaises(AuthorizationFailure):
            self.authorization.require_task_execution(execution.execution_id)
        with self.assertRaises(LeaseUnavailable):
            self.checkpoints.acquire_lease(
                execution.execution_id,
                worker_id="disabled-owner-worker",
                now=NOW,
                ttl_seconds=30,
            )


if __name__ == "__main__":
    unittest.main()
