"""Timed-heartbeat Worker Runtime tests in a disposable PostgreSQL database."""

from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Event, Thread
from time import sleep
import unittest
import uuid

import psycopg
from psycopg import sql

from backend.planning.checkpoint_recovery import initial_agent_state
from backend.planning.checkpoint_store import LeaseUnavailable, PostgresCheckpointStore
from backend.planning.demo import build_quarter_sales_comparison_plan
from backend.planning.models import PlanningRequest
from backend.planning.reconciliation import ReconciliationPolicy, ReconciliationService
from backend.planning.worker_runtime import (
    DurableWorkerRuntime,
    DurableWorkerSettings,
)
from backend.tools.models import StrictModel
from backend.tools.registry import ToolAuthorizationPolicy, ToolDefinition, ToolRegistry
from scripts.migrate import apply_migrations, connect_kwargs


SCHEMA = "agent_runtime"


class FakeSalesInput(StrictModel):
    period: str
    region: str


class FakeSalesOutput(StrictModel):
    verified: bool


class DurableWorkerRuntimeDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin_kwargs = connect_kwargs()
        cls.database_name = "eac_test_worker_runtime_" + uuid.uuid4().hex
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

    def new_store(self):
        return PostgresCheckpointStore(
            self.connection_factory,
            schema=SCHEMA,
            idempotency_secret=b"worker-runtime-test-secret-at-least-32-bytes",
        )

    def runtime(self, store=None, *, timeout_ms=2_000):
        return DurableWorkerRuntime(
            store or self.store,
            settings=DurableWorkerSettings(
                enabled=True,
                lease_ttl_seconds=1,
                heartbeat_interval_ms=50,
                operation_timeout_ms=timeout_ms,
            ),
        )

    def create(self, key):
        now = datetime.now(UTC)
        return self.store.create_or_get_execution(
            scope_key="tenant-a:analytics-copilot",
            idempotency_key=key,
            request_sha256="a" * 64,
            plan=self.plan,
            initial_state=initial_agent_state(self.plan),
            policy_version="bounded-plan-policy-v1",
            runtime_version="timed-worker-runtime-v1",
            now=now,
        ).execution

    @staticmethod
    def registry():
        return ToolRegistry(
            (
                ToolDefinition(
                    name="get_postgres_sales_summary",
                    version="test",
                    description="Fake sales output for recovery",
                    input_model=FakeSalesInput,
                    output_model=FakeSalesOutput,
                    handler=lambda value: {"verified": True},
                ),
            ),
            ToolAuthorizationPolicy(
                grants={"analytics-copilot": frozenset({"get_postgres_sales_summary"})}
            ),
        )

    def test_long_operation_is_heartbeated_and_committed_once(self):
        execution = self.create("long-operation")
        calls = []
        def operation(step):
            calls.append(step.step_id)
            sleep(0.24)
            return {"verified": True}
        result = self.runtime().run_next(
            execution.execution_id, worker_id="worker-one", operation=operation
        )
        self.assertEqual(result.outcome, "step_completed")
        self.assertGreaterEqual(result.heartbeat_count, 2)
        self.assertEqual(calls, ["load_baseline_sales"])
        receipt = self.store.list_receipts(execution.execution_id)[0]
        self.assertEqual(receipt.status, "succeeded")
        self.assertIsNone(self.store.get_execution(execution.execution_id).lease_owner)

    def test_concurrent_worker_cannot_take_live_heartbeated_lease(self):
        execution = self.create("concurrent-workers")
        entered = Event()
        results = {}
        def long_operation(step):
            entered.set()
            sleep(0.3)
            return {"verified": True}
        thread = Thread(
            target=lambda: results.setdefault(
                "first",
                self.runtime().run_next(
                    execution.execution_id,
                    worker_id="worker-one",
                    operation=long_operation,
                ),
            )
        )
        thread.start()
        self.assertTrue(entered.wait(1))
        second_calls = []
        second = self.runtime(self.new_store()).run_next(
            execution.execution_id,
            worker_id="worker-two",
            operation=lambda step: second_calls.append(step.step_id) or {},
        )
        thread.join(2)
        self.assertEqual(second.outcome, "lease_unavailable")
        self.assertEqual(second_calls, [])
        self.assertEqual(results["first"].outcome, "step_completed")

    def test_heartbeat_failure_stops_old_worker_and_takeover_reconciles(self):
        execution = self.create("heartbeat-failure")
        original_renew = self.store.renew_lease
        self.store.renew_lease = lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("simulated database heartbeat failure")
        )
        result = self.runtime().run_next(
            execution.execution_id,
            worker_id="worker-one",
            operation=lambda step: sleep(0.2) or {"verified": True},
        )
        self.store.renew_lease = original_renew
        self.assertEqual(result.outcome, "lease_lost")
        sleep(1.05)
        takeover_calls = []
        recovered = self.runtime(self.new_store()).run_next(
            execution.execution_id,
            worker_id="worker-two",
            operation=lambda step: takeover_calls.append(step.step_id) or {},
        )
        self.assertEqual(recovered.outcome, "reconciliation_required")
        self.assertEqual(takeover_calls, [])
        checkpoint = self.store.get_latest_checkpoint(execution.execution_id)
        with self.assertRaises(LeaseUnavailable):
            self.store.save_checkpoint(
                execution.execution_id,
                worker_id="worker-one",
                expected_state_version=1,
                execution_status="running",
                agent_state=checkpoint.agent_state,
                now=datetime.now(UTC),
            )

    def test_crash_restart_reconciliation_resumes_without_replay(self):
        execution = self.create("crash-restart")
        calls = []
        crashed = self.runtime().run_next(
            execution.execution_id,
            worker_id="worker-one",
            operation=lambda step: calls.append(step.step_id) or {"verified": True},
            crash_at="after_operation",
        )
        self.assertEqual(crashed.outcome, "crashed")
        sleep(1.05)
        recovered = self.runtime(self.new_store()).run_next(
            execution.execution_id,
            worker_id="worker-two",
            operation=lambda step: (_ for _ in ()).throw(AssertionError("must not replay")),
        )
        self.assertEqual(recovered.outcome, "reconciliation_required")
        service = ReconciliationService(
            self.new_store(),
            ReconciliationPolicy(allowed_actors=frozenset({"reconciliation-worker"})),
            self.registry(),
        )
        resolution = service.confirm_succeeded(
            execution.execution_id,
            step_id="load_baseline_sales",
            expected_state_version=recovered.state_version,
            output={"verified": True},
            tool_call_id="verified-external-call",
            evidence_sha256="e" * 64,
            actor_id="reconciliation-worker",
            now=datetime.now(UTC),
        )
        self.assertEqual(resolution.execution.status, "running")
        resumed_calls = []
        resumed = self.runtime(self.new_store()).run_next(
            execution.execution_id,
            worker_id="worker-three",
            operation=lambda step: resumed_calls.append(step.step_id) or {"verified": True},
        )
        self.assertEqual(resumed.outcome, "step_completed")
        self.assertEqual(resumed_calls, ["load_comparison_sales"])
        self.assertEqual(calls, ["load_baseline_sales"])

    def test_post_checkpoint_crash_takeover_starts_next_step_without_replay(self):
        execution = self.create("checkpoint-crash")
        calls = []
        crashed = self.runtime().run_next(
            execution.execution_id,
            worker_id="worker-one",
            operation=lambda step: calls.append(step.step_id) or {"verified": True},
            crash_at="after_checkpoint",
        )
        self.assertEqual(crashed.outcome, "crashed")
        sleep(1.05)
        resumed = self.runtime(self.new_store()).run_next(
            execution.execution_id,
            worker_id="worker-two",
            operation=lambda step: calls.append(step.step_id) or {"verified": True},
        )
        self.assertEqual(resumed.outcome, "step_completed")
        self.assertEqual(calls, ["load_baseline_sales", "load_comparison_sales"])

    def test_operation_timeout_becomes_unknown_without_retry(self):
        execution = self.create("operation-timeout")
        calls = []
        result = self.runtime(timeout_ms=120).run_next(
            execution.execution_id,
            worker_id="worker-one",
            operation=lambda step: calls.append(step.step_id) or sleep(0.25) or {"verified": True},
        )
        self.assertEqual(result.outcome, "reconciliation_required")
        sleep(0.2)
        self.assertEqual(calls, ["load_baseline_sales"])
        receipt = self.store.list_receipts(execution.execution_id)[0]
        self.assertEqual(receipt.status, "ambiguous")
        again = self.runtime(self.new_store()).run_next(
            execution.execution_id,
            worker_id="worker-two",
            operation=lambda step: (_ for _ in ()).throw(AssertionError("must not retry")),
        )
        self.assertEqual(again.outcome, "reconciliation_required")


if __name__ == "__main__":
    unittest.main()
