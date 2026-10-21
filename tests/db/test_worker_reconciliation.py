"""Phase 5.5c worker fencing and reconciliation in a disposable database."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import unittest
import uuid

import psycopg
from psycopg import sql

from backend.planning.checkpoint_recovery import CrashRecoverySimulator, initial_agent_state
from backend.planning.checkpoint_store import CheckpointStoreError, LeaseUnavailable, PostgresCheckpointStore
from backend.planning.demo import build_quarter_sales_comparison_plan
from backend.planning.models import PlanningRequest
from backend.planning.reconciliation import (
    ReconciliationAuthorizationError,
    ReconciliationPolicy,
    ReconciliationService,
)
from backend.planning.worker_coordination import WorkerLeaseCoordinator
from backend.tools.models import StrictModel
from backend.tools.registry import (
    ToolAuthorizationPolicy,
    ToolDefinition,
    ToolRegistry,
)
from scripts.migrate import apply_migrations, connect_kwargs


SCHEMA = "agent_runtime"
EVIDENCE_SHA = "e" * 64


class VerifiedToolOutput(StrictModel):
    verified: bool


class VerifiedToolInput(StrictModel):
    period: str
    region: str


class WorkerReconciliationDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin_kwargs = connect_kwargs()
        cls.database_name = "eac_test_worker_reconcile_" + uuid.uuid4().hex
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
                f"TRUNCATE {SCHEMA}.reconciliation_events,{SCHEMA}.step_receipts,"
                f"{SCHEMA}.checkpoints,{SCHEMA}.executions CASCADE"
            )
        self.store = self.new_store()
        self.plan = build_quarter_sales_comparison_plan(PlanningRequest())
        self.now = datetime(2026, 10, 21, 3, 0, tzinfo=UTC)
        self.service = self.new_service(self.store)

    def new_store(self):
        return PostgresCheckpointStore(
            self.connection_factory,
            schema=SCHEMA,
            idempotency_secret=b"phase55c-test-secret-is-at-least-32-bytes",
        )

    @staticmethod
    def new_service(store):
        registry = ToolRegistry(
            (
                ToolDefinition(
                    name="get_postgres_sales_summary",
                    version="test",
                    description="Deterministic fake reconciliation tool",
                    input_model=VerifiedToolInput,
                    output_model=VerifiedToolOutput,
                    handler=lambda value: {"verified": True},
                ),
            ),
            ToolAuthorizationPolicy(
                grants={"analytics-copilot": frozenset({"get_postgres_sales_summary"})}
            ),
        )
        return ReconciliationService(
            store,
            ReconciliationPolicy(allowed_actors=frozenset({"reconciliation-worker"})),
            registry,
        )

    def create(self, key="worker-reconcile-request"):
        return self.store.create_or_get_execution(
            scope_key="tenant-a:analytics-copilot",
            idempotency_key=key,
            request_sha256="a" * 64,
            plan=self.plan,
            initial_state=initial_agent_state(self.plan),
            policy_version="bounded-plan-policy-v1",
            runtime_version="durable-controlled-planner-v1",
            now=self.now,
        ).execution

    def ambiguous_execution(self, key="ambiguous-request"):
        execution = self.create(key)
        external_effects = []
        simulator = CrashRecoverySimulator(self.store, lease_ttl_seconds=10)
        crashed = simulator.run_next(
            execution.execution_id,
            worker_id="worker-one",
            now=self.now,
            operation=lambda step: external_effects.append(step.step_id) or {"verified": True},
            crash_at="after_operation",
        )
        self.assertEqual(crashed.operation_calls, 1)
        recovered = CrashRecoverySimulator(self.new_store(), lease_ttl_seconds=10).run_next(
            execution.execution_id,
            worker_id="worker-two",
            now=self.now + timedelta(seconds=11),
            operation=lambda step: (_ for _ in ()).throw(AssertionError("must not retry")),
        )
        self.assertEqual(recovered.outcome, "reconciliation_required")
        self.assertEqual(external_effects, ["load_baseline_sales"])
        return execution.execution_id, recovered.state_version

    def test_reconciliation_migration_is_private(self):
        with psycopg.connect(**self.database_kwargs) as database:
            applied = database.execute(
                "SELECT count(*) FROM app_migrations.applied WHERE name='006_planning_reconciliation.sql'"
            ).fetchone()[0]
            public_access = database.execute(
                "SELECT has_table_privilege('public',%s,'SELECT')",
                (f"{SCHEMA}.reconciliation_events",),
            ).fetchone()[0]
        self.assertEqual(applied, 1)
        self.assertFalse(public_access)

    def test_heartbeat_fences_expired_worker_and_takeover(self):
        execution = self.create("heartbeat-fencing")
        coordinator = WorkerLeaseCoordinator(self.store, ttl_seconds=10)
        session = coordinator.claim(execution.execution_id, worker_id="worker-one", now=self.now)
        session = coordinator.heartbeat(session, now=self.now + timedelta(seconds=8))
        with self.assertRaises(LeaseUnavailable):
            WorkerLeaseCoordinator(self.new_store(), ttl_seconds=10).claim(
                execution.execution_id, worker_id="worker-two", now=self.now + timedelta(seconds=17)
            )
        takeover = WorkerLeaseCoordinator(self.new_store(), ttl_seconds=10).claim(
            execution.execution_id, worker_id="worker-two", now=self.now + timedelta(seconds=19)
        )
        self.assertEqual(takeover.worker_id, "worker-two")
        with self.assertRaises(LeaseUnavailable):
            coordinator.assert_current(session, now=self.now + timedelta(seconds=19))
        with self.assertRaises(LeaseUnavailable):
            coordinator.heartbeat(session, now=self.now + timedelta(seconds=19))
        checkpoint = self.store.get_latest_checkpoint(execution.execution_id)
        with self.assertRaises(LeaseUnavailable):
            self.store.save_checkpoint(
                execution.execution_id,
                worker_id="worker-one",
                expected_state_version=session.state_version,
                execution_status="running",
                agent_state=checkpoint.agent_state,
                now=self.now + timedelta(seconds=19),
            )

    def test_unknown_success_is_verified_once_then_execution_resumes_without_replay(self):
        execution_id, version = self.ambiguous_execution("verified-success")
        restarted_service = self.new_service(self.new_store())
        resolution = restarted_service.confirm_succeeded(
            execution_id,
            step_id="load_baseline_sales",
            expected_state_version=version,
            output={"verified": True},
            tool_call_id="external-call-001",
            evidence_sha256=EVIDENCE_SHA,
            actor_id="reconciliation-worker",
            now=self.now + timedelta(seconds=12),
        )
        self.assertEqual(resolution.decision, "confirmed_succeeded")
        self.assertEqual(resolution.execution.status, "running")
        self.assertEqual(resolution.receipt.status, "succeeded")
        audit = self.new_store().audit_execution_consistency(execution_id)
        self.assertEqual(audit.completed_receipt_count, 1)
        calls = []
        resumed = CrashRecoverySimulator(self.new_store(), lease_ttl_seconds=10).run_next(
            execution_id,
            worker_id="worker-three",
            now=self.now + timedelta(seconds=13),
            operation=lambda step: calls.append(step.step_id) or {"verified": True},
        )
        self.assertEqual(resumed.step_id, "load_comparison_sales")
        self.assertEqual(calls, ["load_comparison_sales"])
        with self.assertRaises(CheckpointStoreError):
            restarted_service.confirm_succeeded(
                execution_id,
                step_id="load_baseline_sales",
                expected_state_version=version,
                output={"verified": True},
                tool_call_id="external-call-001",
                evidence_sha256=EVIDENCE_SHA,
                actor_id="reconciliation-worker",
                now=self.now + timedelta(seconds=14),
            )

    def test_verified_failure_is_terminal_and_never_retried(self):
        execution_id, version = self.ambiguous_execution("verified-failure")
        resolution = self.service.confirm_failed(
            execution_id,
            step_id="load_baseline_sales",
            expected_state_version=version,
            evidence_sha256="f" * 64,
            actor_id="reconciliation-worker",
            now=self.now + timedelta(seconds=12),
        )
        self.assertEqual(resolution.execution.status, "failed")
        self.assertEqual(resolution.receipt.status, "failed")
        calls = []
        result = CrashRecoverySimulator(self.new_store(), lease_ttl_seconds=10).run_next(
            execution_id,
            worker_id="worker-three",
            now=self.now + timedelta(seconds=13),
            operation=lambda step: calls.append(step.step_id) or {},
        )
        self.assertEqual(result.outcome, "terminal")
        self.assertEqual(calls, [])

    def test_unauthorized_and_concurrent_reconciliation_fail_closed(self):
        execution_id, version = self.ambiguous_execution("concurrent-reconcile")
        with self.assertRaises(ReconciliationAuthorizationError):
            self.service.confirm_succeeded(
                execution_id,
                step_id="load_baseline_sales",
                expected_state_version=version,
                output={"verified": True},
                tool_call_id="external-call-002",
                evidence_sha256=EVIDENCE_SHA,
                actor_id="ordinary-worker",
                now=self.now + timedelta(seconds=12),
            )
        with self.assertRaisesRegex(ValueError, "tool output is invalid"):
            self.service.confirm_succeeded(
                execution_id,
                step_id="load_baseline_sales",
                expected_state_version=version,
                output={"forged": True},
                tool_call_id="external-call-invalid",
                evidence_sha256=EVIDENCE_SHA,
                actor_id="reconciliation-worker",
                now=self.now + timedelta(seconds=12),
            )
        def resolve(_):
            try:
                return self.new_service(self.new_store()).confirm_succeeded(
                    execution_id,
                    step_id="load_baseline_sales",
                    expected_state_version=version,
                    output={"verified": True},
                    tool_call_id="external-call-002",
                    evidence_sha256=EVIDENCE_SHA,
                    actor_id="reconciliation-worker",
                    now=self.now + timedelta(seconds=12),
                ).decision
            except CheckpointStoreError:
                return "rejected"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(resolve, range(2)))
        self.assertEqual(results.count("confirmed_succeeded"), 1)
        self.assertEqual(results.count("rejected"), 1)
        with psycopg.connect(**self.database_kwargs) as database:
            count = database.execute(
                f"SELECT count(*) FROM {SCHEMA}.reconciliation_events WHERE execution_id=%s",
                (execution_id,),
            ).fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
