"""Phase 5.3 tests use a unique disposable PostgreSQL database only."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
import unittest
import uuid

import psycopg
from psycopg import sql

from backend.planning.checkpoint_recovery import (
    CrashRecoverySimulator,
    initial_agent_state,
)
from backend.planning.checkpoint_store import (
    CheckpointConflict,
    IdempotencyConflict,
    LeaseUnavailable,
    PostgresCheckpointStore,
    ReceiptConflict,
)
from backend.planning.demo import build_quarter_sales_comparison_plan
from backend.planning.models import PlanningRequest
from scripts.migrate import connect_kwargs


FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "planning_checkpoint_v1.sql"
)
SCHEMA = "planning_checkpoint_test"


class PlanningCheckpointDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin_kwargs = connect_kwargs()
        cls.database_name = "eac_test_checkpoint_" + uuid.uuid4().hex
        with psycopg.connect(**cls.admin_kwargs, autocommit=True) as admin:
            admin.execute(
                sql.SQL("CREATE DATABASE {}").format(
                    sql.Identifier(cls.database_name)
                )
            )
        cls.addClassCleanup(cls.cleanup_database)
        cls.database_kwargs = {
            **cls.admin_kwargs,
            "dbname": cls.database_name,
        }
        with psycopg.connect(**cls.database_kwargs, autocommit=True) as database:
            database.execute(FIXTURE.read_text(encoding="utf-8"))

    @classmethod
    def cleanup_database(cls):
        with psycopg.connect(**cls.admin_kwargs, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {}").format(
                    sql.Identifier(cls.database_name)
                )
            )

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
        self.store = PostgresCheckpointStore(
            self.connection_factory,
            schema=SCHEMA,
            idempotency_secret=b"phase5-test-secret-is-at-least-32-bytes",
        )
        self.simulator = CrashRecoverySimulator(self.store, lease_ttl_seconds=10)
        self.plan = build_quarter_sales_comparison_plan(PlanningRequest())
        self.now = datetime(2026, 10, 20, 1, 0, tzinfo=UTC)

    def create(self, key="checkpoint-key-001", request_hash=None):
        return self.store.create_or_get_execution(
            scope_key="tenant-a:analytics-copilot",
            idempotency_key=key,
            request_sha256=request_hash or "a" * 64,
            plan=self.plan,
            initial_state=initial_agent_state(self.plan),
            policy_version="bounded-plan-policy-v1",
            runtime_version="checkpoint-simulator-v1",
            now=self.now,
        )

    @staticmethod
    def operation_counter():
        calls = {}

        def operation(step):
            calls[step.step_id] = calls.get(step.step_id, 0) + 1
            return {
                "step_id": step.step_id,
                "synthetic_result": calls[step.step_id],
            }

        return calls, operation

    def test_creation_rejects_initial_state_not_bound_to_exact_plan(self):
        invalid_state = initial_agent_state(self.plan).model_copy(
            update={"step_states": initial_agent_state(self.plan).step_states[:-1]}
        )
        with self.assertRaisesRegex(ValueError, "initial checkpoint does not match plan"):
            self.store.create_or_get_execution(
                scope_key="tenant-a:analytics-copilot",
                idempotency_key="invalid-initial-state",
                request_sha256="a" * 64,
                plan=self.plan,
                initial_state=invalid_state,
                policy_version="bounded-plan-policy-v1",
                runtime_version="checkpoint-simulator-v1",
                now=self.now,
            )

    def test_execution_checkpoint_and_scoped_idempotency_are_persistent(self):
        first = self.create()
        second = self.create()
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.execution.execution_id, second.execution.execution_id)
        self.assertEqual(first.checkpoint.sequence, 0)
        self.assertEqual(first.checkpoint.agent_state.status, "pending")
        self.assertNotEqual(
            first.execution.idempotency_key_hash,
            "checkpoint-key-001",
        )
        different_scope = self.store.create_or_get_execution(
            scope_key="tenant-b:analytics-copilot",
            idempotency_key="checkpoint-key-001",
            request_sha256="a" * 64,
            plan=self.plan,
            initial_state=initial_agent_state(self.plan),
            policy_version="bounded-plan-policy-v1",
            runtime_version="checkpoint-simulator-v1",
            now=self.now,
        )
        self.assertTrue(different_scope.created)
        self.assertNotEqual(
            first.execution.execution_id, different_scope.execution.execution_id
        )
        self.assertNotEqual(
            first.execution.idempotency_key_hash,
            different_scope.execution.idempotency_key_hash,
        )
        with self.assertRaises(IdempotencyConflict):
            self.create(request_hash="b" * 64)
        with psycopg.connect(**self.database_kwargs) as database:
            counts = database.execute(
                f"""SELECT
                    (SELECT count(*) FROM {SCHEMA}.executions),
                    (SELECT count(*) FROM {SCHEMA}.checkpoints),
                    (SELECT count(*) FROM {SCHEMA}.step_receipts)"""
            ).fetchone()
            raw_key_present = database.execute(
                f"SELECT count(*) FROM {SCHEMA}.executions WHERE idempotency_key_hash=%s",
                ("checkpoint-key-001",),
            ).fetchone()[0]
        self.assertEqual(counts, (2, 2, 0))
        self.assertEqual(raw_key_present, 0)

    def test_lease_exclusion_and_expiry_takeover(self):
        execution = self.create().execution
        first = self.store.acquire_lease(
            execution.execution_id,
            worker_id="worker-one",
            now=self.now,
            ttl_seconds=10,
        )
        self.assertEqual(first.lease_owner, "worker-one")
        with self.assertRaises(LeaseUnavailable):
            self.store.acquire_lease(
                execution.execution_id,
                worker_id="worker-two",
                now=self.now + timedelta(seconds=9),
                ttl_seconds=10,
            )
        takeover = self.store.acquire_lease(
            execution.execution_id,
            worker_id="worker-two",
            now=self.now + timedelta(seconds=11),
            ttl_seconds=10,
        )
        self.assertEqual(takeover.lease_owner, "worker-two")

    def test_state_version_compare_and_swap_rejects_stale_writer(self):
        execution = self.create().execution
        leased = self.store.acquire_lease(
            execution.execution_id,
            worker_id="worker-one",
            now=self.now,
            ttl_seconds=10,
        )
        initial = self.store.get_latest_checkpoint(execution.execution_id)
        first_step = self.plan.steps[0]
        running = self.simulator._running_state(initial.agent_state, first_step)
        token = self.store.derive_step_token(
            execution.execution_id,
            execution.plan_sha256,
            first_step.step_id,
        )
        started = self.store.start_step(
            execution.execution_id,
            worker_id="worker-one",
            expected_state_version=leased.state_version,
            running_state=running,
            step_id=first_step.step_id,
            action_name=first_step.tool_call.name,
            input_payload=first_step.tool_call.arguments,
            idempotency_token=token,
            now=self.now,
        )
        self.assertEqual(started.execution.state_version, 1)
        with self.assertRaisesRegex(CheckpointConflict, "checkpoint_cas_conflict"):
            self.store.save_checkpoint(
                execution.execution_id,
                worker_id="worker-one",
                expected_state_version=0,
                execution_status="running",
                agent_state=running,
                now=self.now + timedelta(milliseconds=1),
            )

    def test_success_receipt_deduplicates_and_full_execution_completes(self):
        execution = self.create().execution
        calls, operation = self.operation_counter()
        result = None
        for index in range(3):
            result = self.simulator.run_next(
                execution.execution_id,
                worker_id=f"worker-{index}",
                now=self.now + timedelta(seconds=index),
                operation=operation,
            )
        self.assertEqual(result.outcome, "execution_completed")
        self.assertEqual(sum(calls.values()), 3)
        terminal = self.simulator.run_next(
            execution.execution_id,
            worker_id="worker-terminal",
            now=self.now + timedelta(seconds=5),
            operation=operation,
        )
        self.assertEqual(terminal.outcome, "terminal")
        self.assertEqual(terminal.operation_calls, 0)
        self.assertEqual(sum(calls.values()), 3)
        receipts = self.store.list_receipts(execution.execution_id)
        self.assertEqual(len(receipts), 3)
        self.assertTrue(all(item.status == "succeeded" for item in receipts))
        final = self.store.get_execution(execution.execution_id)
        self.assertEqual(final.status, "completed")
        self.assertEqual(final.state_version, 6)
        self.assertIsNone(final.lease_owner)

    def test_success_receipt_deduplicates_under_a_valid_lease(self):
        execution = self.create().execution
        calls, operation = self.operation_counter()
        first = self.simulator.run_next(
            execution.execution_id,
            worker_id="worker-one",
            now=self.now,
            operation=operation,
        )
        self.assertEqual(first.outcome, "step_completed")
        leased = self.store.acquire_lease(
            execution.execution_id,
            worker_id="worker-two",
            now=self.now + timedelta(seconds=1),
            ttl_seconds=10,
        )
        step = self.plan.steps[0]
        token = self.store.derive_step_token(
            execution.execution_id, execution.plan_sha256, step.step_id
        )
        replay = self.store.start_step(
            execution.execution_id,
            worker_id="worker-two",
            expected_state_version=leased.state_version,
            running_state=self.store.get_latest_checkpoint(
                execution.execution_id
            ).agent_state,
            step_id=step.step_id,
            action_name=step.tool_call.name,
            input_payload=step.tool_call.arguments,
            idempotency_token=token,
            now=self.now + timedelta(seconds=1),
        )
        self.assertTrue(replay.deduplicated)
        self.assertIsNone(replay.checkpoint)
        self.assertEqual(replay.receipt.status, "succeeded")
        self.assertEqual(calls, {"load_baseline_sales": 1})
        self.assertEqual(
            self.store.get_execution(execution.execution_id).state_version,
            leased.state_version,
        )
        self.store.release_lease(
            execution.execution_id,
            worker_id="worker-two",
            now=self.now + timedelta(seconds=1, milliseconds=1),
        )

    def test_crash_after_checkpoint_resumes_without_repeating_completed_step(self):
        execution = self.create().execution
        calls, operation = self.operation_counter()
        crashed = self.simulator.run_next(
            execution.execution_id,
            worker_id="worker-one",
            now=self.now,
            operation=operation,
            crash_at="after_checkpoint",
        )
        self.assertEqual(crashed.outcome, "crashed")
        self.assertEqual(calls, {"load_baseline_sales": 1})
        blocked = self.simulator.run_next(
            execution.execution_id,
            worker_id="worker-two",
            now=self.now + timedelta(seconds=5),
            operation=operation,
        )
        self.assertEqual(blocked.outcome, "lease_unavailable")
        resumed = self.simulator.run_next(
            execution.execution_id,
            worker_id="worker-two",
            now=self.now + timedelta(seconds=11),
            operation=operation,
        )
        self.assertEqual(resumed.step_id, "load_comparison_sales")
        self.assertEqual(calls["load_baseline_sales"], 1)
        self.assertEqual(calls["load_comparison_sales"], 1)

    def test_unknown_outcome_becomes_ambiguous_and_is_never_retried(self):
        execution = self.create().execution
        calls, operation = self.operation_counter()
        crashed = self.simulator.run_next(
            execution.execution_id,
            worker_id="worker-one",
            now=self.now,
            operation=operation,
            crash_at="after_operation",
        )
        self.assertEqual(crashed.outcome, "crashed")
        self.assertEqual(sum(calls.values()), 1)
        recovered = self.simulator.run_next(
            execution.execution_id,
            worker_id="worker-two",
            now=self.now + timedelta(seconds=11),
            operation=operation,
        )
        self.assertEqual(recovered.outcome, "reconciliation_required")
        self.assertEqual(recovered.operation_calls, 0)
        self.assertEqual(sum(calls.values()), 1)
        again = self.simulator.run_next(
            execution.execution_id,
            worker_id="worker-three",
            now=self.now + timedelta(seconds=20),
            operation=operation,
        )
        self.assertEqual(again.outcome, "reconciliation_required")
        self.assertEqual(sum(calls.values()), 1)
        final = self.store.get_execution(execution.execution_id)
        receipt = self.store.list_receipts(execution.execution_id)[0]
        self.assertEqual(final.status, "reconciling")
        self.assertEqual(receipt.status, "ambiguous")
        self.assertEqual(receipt.error_code, "outcome_unknown")

    def test_crash_after_start_is_conservatively_ambiguous(self):
        execution = self.create().execution
        calls, operation = self.operation_counter()
        crashed = self.simulator.run_next(
            execution.execution_id,
            worker_id="worker-one",
            now=self.now,
            operation=operation,
            crash_at="after_start",
        )
        self.assertEqual(crashed.operation_calls, 0)
        recovered = self.simulator.run_next(
            execution.execution_id,
            worker_id="worker-two",
            now=self.now + timedelta(seconds=11),
            operation=operation,
        )
        self.assertEqual(recovered.outcome, "reconciliation_required")
        self.assertEqual(calls, {})

    def test_content_hash_tampering_fails_closed_on_read(self):
        plan_execution = self.create(key="plan-integrity-key").execution
        with psycopg.connect(**self.database_kwargs) as database:
            database.execute(
                f"UPDATE {SCHEMA}.executions SET plan_sha256=%s WHERE execution_id=%s",
                ("b" * 64, plan_execution.execution_id),
            )
        with self.assertRaisesRegex(
            CheckpointConflict, "execution_plan_integrity_invalid"
        ):
            self.store.get_execution(plan_execution.execution_id)

        checkpoint_execution = self.create(key="checkpoint-integrity-key").execution
        with psycopg.connect(**self.database_kwargs) as database:
            database.execute(
                f"UPDATE {SCHEMA}.checkpoints SET state_sha256=%s WHERE execution_id=%s",
                ("b" * 64, checkpoint_execution.execution_id),
            )
        with self.assertRaisesRegex(
            CheckpointConflict, "checkpoint_integrity_invalid"
        ):
            self.store.get_latest_checkpoint(checkpoint_execution.execution_id)

        receipt_execution = self.create(key="receipt-integrity-key").execution
        calls, operation = self.operation_counter()
        self.simulator.run_next(
            receipt_execution.execution_id,
            worker_id="worker-integrity",
            now=self.now,
            operation=operation,
        )
        with psycopg.connect(**self.database_kwargs) as database:
            database.execute(
                f"UPDATE {SCHEMA}.step_receipts SET output_json=%s WHERE execution_id=%s",
                (psycopg.types.json.Jsonb({"tampered": True}), receipt_execution.execution_id),
            )
        with self.assertRaisesRegex(
            ReceiptConflict, "step_receipt_integrity_invalid"
        ):
            self.store.list_receipts(receipt_execution.execution_id)

    def test_test_fixture_is_not_a_production_migration(self):
        migration_names = {
            path.name
            for path in (Path(__file__).resolve().parents[2] / "migrations").glob("*.sql")
        }
        self.assertNotIn(FIXTURE.name, migration_names)
        with psycopg.connect(**self.database_kwargs) as database:
            public_access = database.execute(
                """SELECT has_schema_privilege('public',%s,'USAGE'),
                          has_table_privilege('public',%s,'SELECT')""",
                (SCHEMA, f"{SCHEMA}.executions"),
            ).fetchone()
        self.assertEqual(public_access, (False, False))


if __name__ == "__main__":
    unittest.main()
