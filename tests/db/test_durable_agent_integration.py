"""Phase 5.5e durable-agent integration tests in disposable PostgreSQL."""

from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from threading import Event, Lock, Thread
from time import sleep
import unittest
import uuid

import psycopg
from psycopg import sql

from backend.planning.checkpoint_store import LeaseUnavailable, PostgresCheckpointStore
from backend.planning.demo import build_quarter_sales_comparison_plan
from backend.planning.durable_agent_runtime import (
    DurableAgentRuntime,
    DurableAgentSettings,
)
from backend.planning.durable_controlled_runtime import (
    DurableControlledPlanner,
    DurableControlledPlannerSettings,
    DurablePlannerError,
)
from backend.planning.models import PlanningRequest
from backend.planning.reconciliation import ReconciliationPolicy, ReconciliationService
from backend.planning.worker_runtime import DurableWorkerRuntime, DurableWorkerSettings
from backend.tools.catalog import build_tool_registry
from backend.tools.executor import ToolExecutor
from scripts.migrate import apply_migrations, connect_kwargs
from scripts.seed_sales import seed_sales


SCHEMA = "agent_runtime"
IDEMPOTENCY_SECRET = b"phase55e-test-secret-is-at-least-32-bytes"


class FakePlanProvider:
    def __init__(self, transform=None):
        self.calls = 0
        self._transform = transform

    def propose(self, request: PlanningRequest) -> str:
        self.calls += 1
        plan = build_quarter_sales_comparison_plan(request)
        if self._transform is not None:
            plan = self._transform(plan)
        return plan.model_dump_json()


class RecordingExecutor:
    """Record safe metadata while delegating to the real ToolExecutor."""

    def __init__(self, delegate: ToolExecutor, *, delay_seconds: float = 0):
        self._delegate = delegate
        self._delay_seconds = delay_seconds
        self._lock = Lock()
        self.entered = Event()
        self.calls = []
        self.successful_outputs = []

    def execute(self, call, context):
        with self._lock:
            self.calls.append((call.name, dict(call.arguments)))
        self.entered.set()
        if self._delay_seconds:
            sleep(self._delay_seconds)
        result = self._delegate.execute(call, context)
        if result.status == "success":
            with self._lock:
                self.successful_outputs.append(dict(result.output))
        return result


class DurableAgentIntegrationDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin_kwargs = connect_kwargs()
        cls.database_name = "eac_test_durable_agent_" + uuid.uuid4().hex
        with psycopg.connect(**cls.admin_kwargs, autocommit=True) as admin:
            admin.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.database_name))
            )
        cls.addClassCleanup(cls.cleanup_database)
        cls.database_kwargs = {**cls.admin_kwargs, "dbname": cls.database_name}
        with psycopg.connect(**cls.database_kwargs, autocommit=True) as database:
            apply_migrations(database)
            seed_sales(database)

    @classmethod
    def cleanup_database(cls):
        with psycopg.connect(**cls.admin_kwargs, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {}").format(sql.Identifier(cls.database_name))
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
                f"TRUNCATE {SCHEMA}.reconciliation_events,{SCHEMA}.step_receipts,"
                f"{SCHEMA}.checkpoints,{SCHEMA}.executions CASCADE"
            )

    def new_store(self):
        return PostgresCheckpointStore(
            self.connection_factory,
            schema=SCHEMA,
            idempotency_secret=IDEMPOTENCY_SECRET,
        )

    def new_stack(self, *, delay_seconds=0, provider=None, store=None):
        store = store or self.new_store()
        provider = provider or FakePlanProvider()
        registry = build_tool_registry(self.connection_factory)
        executor = RecordingExecutor(
            ToolExecutor(registry), delay_seconds=delay_seconds
        )
        planner = DurableControlledPlanner(
            provider,
            store,
            executor,
            settings=DurableControlledPlannerSettings(
                enabled=True, lease_ttl_seconds=1
            ),
        )
        worker = DurableWorkerRuntime(
            store,
            settings=DurableWorkerSettings(
                enabled=True,
                lease_ttl_seconds=1,
                heartbeat_interval_ms=50,
                operation_timeout_ms=2_000,
            ),
        )
        agent = DurableAgentRuntime(
            planner,
            worker,
            settings=DurableAgentSettings(enabled=True, max_steps_per_run=8),
        )
        return agent, planner, executor, provider, store, registry

    @staticmethod
    def submit(agent, key):
        return agent.submit(
            PlanningRequest(),
            scope_key="tenant-a:analytics-copilot",
            idempotency_key=key,
            now=datetime.now(UTC),
        )

    def assert_sales_result(self, output):
        self.assertEqual(output.baseline_total_sales, Decimal("400.00"))
        self.assertEqual(output.comparison_total_sales, Decimal("600.00"))
        self.assertEqual(output.absolute_change, Decimal("200.00"))
        self.assertEqual(output.growth_rate_percent, Decimal("50.00"))
        self.assertEqual(output.currency, "USD")
        self.assertEqual(output.region, "all")
        self.assertEqual(output.source_relation, "analytics.sales_orders")

    def test_full_pipeline_uses_real_tools_heartbeats_and_persistent_receipts(self):
        agent, _, executor, provider, store, _ = self.new_stack(delay_seconds=0.13)
        creation = self.submit(agent, "phase55e-happy-path")

        result = agent.run_to_terminal(
            creation.execution.execution_id, worker_id_prefix="happy-worker"
        )

        self.assertEqual(result.status, "completed")
        self.assert_sales_result(result.output)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(
            executor.calls,
            [
                (
                    "get_postgres_sales_summary",
                    {"period": "2026-Q1", "region": "all"},
                ),
                (
                    "get_postgres_sales_summary",
                    {"period": "2026-Q2", "region": "all"},
                ),
            ],
        )
        self.assertGreaterEqual(
            sum(outcome.heartbeat_count for outcome in result.worker_outcomes), 4
        )
        receipts = store.list_receipts(creation.execution.execution_id)
        self.assertEqual([receipt.status for receipt in receipts], ["succeeded"] * 3)
        audit = store.audit_execution_consistency(creation.execution.execution_id)
        self.assertEqual(audit.completed_receipt_count, 3)

    def test_idempotent_submission_and_completed_replay_do_not_repeat_work(self):
        agent, _, executor, provider, _, _ = self.new_stack()
        first = self.submit(agent, "phase55e-idempotent-request")
        second = self.submit(agent, "phase55e-idempotent-request")
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.execution.execution_id, second.execution.execution_id)
        self.assertEqual(provider.calls, 1)

        initial = agent.run_to_terminal(
            first.execution.execution_id, worker_id_prefix="idempotent-worker"
        )
        call_count = len(executor.calls)
        replay = agent.run_to_terminal(
            first.execution.execution_id, worker_id_prefix="replay-worker"
        )

        self.assertEqual(initial.status, "completed")
        self.assertEqual(replay.status, "completed")
        self.assert_sales_result(replay.output)
        self.assertEqual(len(executor.calls), call_count)

    def test_live_heartbeat_prevents_concurrent_worker_takeover(self):
        agent, _, executor, _, _, _ = self.new_stack(delay_seconds=0.25)
        creation = self.submit(agent, "phase55e-concurrent-workers")
        execution_id = creation.execution.execution_id
        results = {}
        first = Thread(
            target=lambda: results.setdefault(
                "first",
                agent.run_next(execution_id, worker_id="concurrent-worker-one"),
            )
        )
        first.start()
        self.assertTrue(executor.entered.wait(1))

        second_agent, _, second_executor, _, _, _ = self.new_stack()
        second = second_agent.run_next(
            execution_id, worker_id="concurrent-worker-two"
        )
        first.join(2)

        self.assertEqual(second.outcome, "lease_unavailable")
        self.assertEqual(second_executor.calls, [])
        self.assertEqual(results["first"].outcome, "step_completed")

    def test_post_checkpoint_crash_resumes_at_next_step_without_replay(self):
        agent, _, executor, _, _, _ = self.new_stack()
        creation = self.submit(agent, "phase55e-checkpoint-crash")
        execution_id = creation.execution.execution_id

        crashed = agent.run_next(
            execution_id,
            worker_id="crash-worker-one",
            crash_at="after_checkpoint",
        )
        self.assertEqual(crashed.outcome, "crashed")
        self.assertEqual(len(executor.calls), 1)
        sleep(1.05)

        restarted, _, restarted_executor, _, _, _ = self.new_stack()
        result = restarted.run_to_terminal(
            execution_id, worker_id_prefix="restart-worker"
        )
        self.assertEqual(result.status, "completed")
        self.assert_sales_result(result.output)
        self.assertEqual(
            restarted_executor.calls,
            [
                (
                    "get_postgres_sales_summary",
                    {"period": "2026-Q2", "region": "all"},
                )
            ],
        )

    def test_lost_heartbeat_requires_reconciliation_before_safe_resume(self):
        agent, _, executor, _, store, _ = self.new_stack(delay_seconds=0.2)
        creation = self.submit(agent, "phase55e-unknown-outcome")
        execution_id = creation.execution.execution_id
        original_renew = store.renew_lease
        store.renew_lease = lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("simulated heartbeat database failure")
        )

        lost = agent.run_next(execution_id, worker_id="lost-worker-one")
        store.renew_lease = original_renew
        self.assertEqual(lost.outcome, "lease_lost")
        sleep(1.25)
        self.assertEqual(len(executor.calls), 1)
        verified_output = executor.successful_outputs[0]

        recovery_agent, _, recovery_executor, _, recovery_store, registry = (
            self.new_stack()
        )
        recovery = recovery_agent.run_next(
            execution_id, worker_id="recovery-worker-two"
        )
        self.assertEqual(recovery.outcome, "reconciliation_required")
        self.assertEqual(recovery_executor.calls, [])
        checkpoint = recovery_store.get_latest_checkpoint(execution_id)
        with self.assertRaises(LeaseUnavailable):
            store.save_checkpoint(
                execution_id,
                worker_id="lost-worker-one",
                expected_state_version=1,
                execution_status="running",
                agent_state=checkpoint.agent_state,
                now=datetime.now(UTC),
            )

        service = ReconciliationService(
            recovery_store,
            ReconciliationPolicy(
                allowed_actors=frozenset({"reconciliation-worker"})
            ),
            registry,
        )
        resolution = service.confirm_succeeded(
            execution_id,
            step_id="load_baseline_sales",
            expected_state_version=recovery.state_version,
            output=verified_output,
            tool_call_id="verified-phase55e-tool-call",
            evidence_sha256="e" * 64,
            actor_id="reconciliation-worker",
            now=datetime.now(UTC),
        )
        self.assertEqual(resolution.execution.status, "running")

        completed = recovery_agent.run_to_terminal(
            execution_id, worker_id_prefix="reconciled-worker"
        )
        self.assertEqual(completed.status, "completed")
        self.assert_sales_result(completed.output)
        self.assertEqual(
            recovery_executor.calls,
            [
                (
                    "get_postgres_sales_summary",
                    {"period": "2026-Q2", "region": "all"},
                )
            ],
        )

    def test_invalid_plan_is_rejected_before_persistence_or_execution(self):
        def unauthorized_plan(plan):
            first = plan.steps[0].model_copy(
                update={
                    "tool_call": plan.steps[0].tool_call.model_copy(
                        update={"name": "get_sales_summary"}
                    )
                }
            )
            return plan.model_copy(update={"steps": (first, *plan.steps[1:])})

        provider = FakePlanProvider(transform=unauthorized_plan)
        agent, _, executor, _, _, _ = self.new_stack(provider=provider)
        with self.assertRaises(DurablePlannerError) as captured:
            self.submit(agent, "phase55e-invalid-plan")
        self.assertEqual(captured.exception.code, "action_not_authorized")
        self.assertEqual(executor.calls, [])


if __name__ == "__main__":
    unittest.main()
