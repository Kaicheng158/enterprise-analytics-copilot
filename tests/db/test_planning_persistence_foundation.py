"""Phase 5.5b durable planning foundation in a disposable database."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import unittest
import uuid

import psycopg
from psycopg import sql

from backend.planning.checkpoint_recovery import CrashRecoverySimulator, initial_agent_state
from backend.planning.checkpoint_store import (
    CheckpointConflict,
    LeaseUnavailable,
    PostgresCheckpointStore,
)
from backend.planning.demo import build_quarter_sales_comparison_plan
from backend.planning.models import PlanningRequest
from scripts.migrate import apply_migrations, connect_kwargs


SCHEMA = "agent_runtime"


class PlanningPersistenceFoundationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin_kwargs = connect_kwargs()
        cls.database_name = "eac_test_planning_runtime_" + uuid.uuid4().hex
        with psycopg.connect(**cls.admin_kwargs, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.database_name)))
        cls.addClassCleanup(cls.cleanup_database)
        cls.database_kwargs = {**cls.admin_kwargs, "dbname": cls.database_name}
        with psycopg.connect(**cls.database_kwargs, autocommit=True) as database:
            apply_migrations(database)

    @classmethod
    def cleanup_database(cls):
        with psycopg.connect(**cls.admin_kwargs, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(cls.database_name)))

    @contextmanager
    def connection_factory(self):
        connection = psycopg.connect(**self.database_kwargs)
        try:
            yield connection
        finally:
            connection.close()

    def setUp(self):
        with psycopg.connect(**self.database_kwargs, autocommit=True) as database:
            database.execute(
                f"TRUNCATE {SCHEMA}.step_receipts,{SCHEMA}.checkpoints,{SCHEMA}.executions CASCADE"
            )
        self.store = self.new_store()
        self.plan = build_quarter_sales_comparison_plan(PlanningRequest())
        self.now = datetime(2026, 10, 21, 1, 0, tzinfo=UTC)

    def new_store(self):
        return PostgresCheckpointStore(
            self.connection_factory,
            schema=SCHEMA,
            idempotency_secret=b"phase55b-test-secret-is-at-least-32-bytes",
        )

    def create(self, store=None, key="persistence-request-001"):
        store = store or self.store
        return store.create_or_get_execution(
            scope_key="tenant-a:analytics-copilot",
            idempotency_key=key,
            request_sha256="a" * 64,
            plan=self.plan,
            initial_state=initial_agent_state(self.plan),
            policy_version="bounded-plan-policy-v1",
            runtime_version="durable-controlled-planner-v1",
            now=self.now,
        )

    @staticmethod
    def operation(step):
        return {"step_id": step.step_id, "synthetic_result": 1}

    def test_versioned_migration_and_permissions(self):
        with psycopg.connect(**self.database_kwargs) as database:
            applied = database.execute(
                "SELECT count(*) FROM app_migrations.applied WHERE name='005_planning_checkpoints.sql'"
            ).fetchone()[0]
            tables = database.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema=%s",
                (SCHEMA,),
            ).fetchone()[0]
            access = database.execute(
                "SELECT has_schema_privilege('public',%s,'USAGE'),has_table_privilege('public',%s,'SELECT')",
                (SCHEMA, f"{SCHEMA}.executions"),
            ).fetchone()
        self.assertEqual(applied, 1)
        self.assertEqual(tables, 3)
        self.assertEqual(access, (False, False))

    def test_lease_heartbeat_blocks_takeover_then_expiry_allows_it(self):
        execution = self.create().execution
        leased = self.store.acquire_lease(
            execution.execution_id, worker_id="worker-one", now=self.now, ttl_seconds=10
        )
        renewed = self.store.renew_lease(
            execution.execution_id,
            worker_id="worker-one",
            expected_state_version=leased.state_version,
            now=self.now + timedelta(seconds=8),
            ttl_seconds=10,
        )
        self.assertEqual(renewed.lease_expires_at, self.now + timedelta(seconds=18))
        with self.assertRaises(LeaseUnavailable):
            self.store.acquire_lease(
                execution.execution_id,
                worker_id="worker-two",
                now=self.now + timedelta(seconds=11),
                ttl_seconds=10,
            )
        takeover = self.store.acquire_lease(
            execution.execution_id,
            worker_id="worker-two",
            now=self.now + timedelta(seconds=19),
            ttl_seconds=10,
        )
        self.assertEqual(takeover.lease_owner, "worker-two")
        with self.assertRaises(LeaseUnavailable):
            self.store.renew_lease(
                execution.execution_id,
                worker_id="worker-one",
                expected_state_version=0,
                now=self.now + timedelta(seconds=20),
                ttl_seconds=10,
            )

    def test_concurrent_lease_acquisition_has_one_winner(self):
        execution = self.create(key="concurrent-lease").execution
        def acquire(worker):
            try:
                return self.new_store().acquire_lease(
                    execution.execution_id, worker_id=worker, now=self.now, ttl_seconds=10
                ).lease_owner
            except LeaseUnavailable:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(acquire, ("worker-one", "worker-two")))
        self.assertEqual(sum(result is not None for result in results), 1)

    def test_restart_duplicate_request_and_checkpoint_receipt_consistency(self):
        first = self.create(key="restart-duplicate")
        restarted_store = self.new_store()
        duplicate = self.create(store=restarted_store, key="restart-duplicate")
        self.assertFalse(duplicate.created)
        self.assertEqual(first.execution.execution_id, duplicate.execution.execution_id)
        simulator = CrashRecoverySimulator(restarted_store, lease_ttl_seconds=10)
        for index in range(3):
            simulator.run_next(
                first.execution.execution_id,
                worker_id=f"worker-{index}",
                now=self.now + timedelta(seconds=index),
                operation=self.operation,
            )
        audit = restarted_store.audit_execution_consistency(first.execution.execution_id)
        self.assertEqual(audit.status, "consistent")
        self.assertEqual(audit.receipt_count, 3)
        self.assertEqual(audit.completed_receipt_count, 3)

    def test_heartbeat_rejects_stale_state_version(self):
        execution = self.create(key="heartbeat-cas").execution
        self.store.acquire_lease(
            execution.execution_id, worker_id="worker-one", now=self.now, ttl_seconds=10
        )
        simulator = CrashRecoverySimulator(self.store, lease_ttl_seconds=10)
        simulator.run_next(
            execution.execution_id,
            worker_id="worker-one",
            now=self.now,
            operation=self.operation,
            crash_at="after_checkpoint",
        )
        with self.assertRaisesRegex(CheckpointConflict, "checkpoint_cas_conflict"):
            self.store.renew_lease(
                execution.execution_id,
                worker_id="worker-one",
                expected_state_version=0,
                now=self.now + timedelta(seconds=1),
                ttl_seconds=10,
            )

    def test_unknown_external_outcome_survives_restart_and_never_retries(self):
        execution = self.create(key="external-unknown").execution
        external_effects = []
        def non_idempotent_operation(step):
            external_effects.append(step.step_id)
            return {"effect_id": len(external_effects)}
        first_process = CrashRecoverySimulator(self.store, lease_ttl_seconds=10)
        crashed = first_process.run_next(
            execution.execution_id,
            worker_id="worker-one",
            now=self.now,
            operation=non_idempotent_operation,
            crash_at="after_operation",
        )
        self.assertEqual(crashed.operation_calls, 1)
        restarted = CrashRecoverySimulator(self.new_store(), lease_ttl_seconds=10)
        recovered = restarted.run_next(
            execution.execution_id,
            worker_id="worker-two",
            now=self.now + timedelta(seconds=11),
            operation=non_idempotent_operation,
        )
        self.assertEqual(recovered.outcome, "reconciliation_required")
        self.assertEqual(external_effects, ["load_baseline_sales"])
        case = self.new_store().get_reconciliation_case(execution.execution_id)
        self.assertFalse(case.retry_allowed)
        self.assertEqual(case.required_action, "verify_external_outcome")
        self.assertEqual(case.reason, "outcome_unknown")


if __name__ == "__main__":
    unittest.main()
