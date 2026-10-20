"""Durable controlled Planner integration in a disposable PostgreSQL database."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import unittest
import uuid

import psycopg
from psycopg import sql

from backend.planning import (
    DurableControlledPlanner,
    DurableControlledPlannerSettings,
    DurablePlannerError,
    PlannerGenerationResponse,
    PlannerUsage,
    PlanningRequest,
    StructuredPlanProvider,
)
from backend.planning.checkpoint_store import CheckpointConflict, PostgresCheckpointStore
from backend.planning.demo import build_quarter_sales_comparison_plan
from backend.tools.models import ToolExecutionResult
from scripts.migrate import connect_kwargs


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "planning_checkpoint_v1.sql"
SCHEMA = "planning_checkpoint_test"


class FakePlannerTransport:
    def __init__(self, plan):
        self.plan = plan
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        return PlannerGenerationResponse(
            request_id=f"durable-fake-{self.calls}",
            content=self.plan.model_dump_json(),
            finish_reason="stop",
            usage=PlannerUsage(input_tokens=100, output_tokens=100, total_tokens=200),
            latency_ms=1,
        )


class FakeAuthorizedSalesExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, call, context):
        if call.name != "get_postgres_sales_summary" or context.principal != "analytics-copilot":
            raise AssertionError("unauthorized durable tool call")
        period = call.arguments["period"]
        region = call.arguments["region"]
        self.calls.append((period, region))
        totals = {"2026-Q1": "400.00", "2026-Q2": "600.00", "2026-Q3": "800.00"}
        return ToolExecutionResult(
            call_id=f"fake-{len(self.calls)}",
            tool_name=call.name,
            tool_version="1.0.0",
            status="success",
            output={
                "period": period,
                "region": region,
                "currency": "USD",
                "order_count": 4,
                "total_sales": totals[period],
                "average_order_value": str(int(totals[period].split(".")[0]) / 4) + "0",
                "data_source": {
                    "kind": "postgresql",
                    "relation": "analytics.sales_orders",
                    "dataset_version": "synthetic-sales-db-v1",
                    "read_only": True,
                },
            },
            error=None,
            latency_ms=1,
        )


class DurableControlledPlannerDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin_kwargs = connect_kwargs()
        cls.database_name = "eac_test_durable_planner_" + uuid.uuid4().hex
        with psycopg.connect(**cls.admin_kwargs, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.database_name)))
        cls.addClassCleanup(cls.cleanup_database)
        cls.database_kwargs = {**cls.admin_kwargs, "dbname": cls.database_name}
        with psycopg.connect(**cls.database_kwargs, autocommit=True) as database:
            database.execute(FIXTURE.read_text())

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
        self.store = PostgresCheckpointStore(
            self.connection_factory,
            schema=SCHEMA,
            idempotency_secret=b"durable-planner-test-secret-32-bytes-minimum",
        )
        self.request = PlanningRequest()
        self.plan = build_quarter_sales_comparison_plan(self.request)
        self.transport = FakePlannerTransport(self.plan)
        self.provider = StructuredPlanProvider(self.transport)
        self.tools = FakeAuthorizedSalesExecutor()
        self.runtime = DurableControlledPlanner(
            self.provider,
            self.store,
            self.tools,
            settings=DurableControlledPlannerSettings(enabled=True, lease_ttl_seconds=10),
        )
        self.now = datetime(2026, 10, 20, 4, 0, tzinfo=UTC)

    def submit(self, key="durable-request-001", request=None):
        return self.runtime.submit(
            request or self.request,
            scope_key="tenant-a:analytics-copilot",
            idempotency_key=key,
            now=self.now,
        )

    def test_submit_persists_validated_generated_plan_and_deduplicates_before_provider(self):
        first = self.submit()
        second = self.submit()
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.execution.execution_id, second.execution.execution_id)
        self.assertEqual(self.provider.calls, 1)
        self.assertEqual(self.transport.calls, 1)
        self.assertEqual(first.execution.plan, self.plan)
        self.assertEqual(first.execution.runtime_version, "durable-controlled-planner-v1")

        with self.assertRaisesRegex(DurablePlannerError, "idempotency_key_conflict"):
            self.runtime.submit(
                PlanningRequest(region="north"),
                scope_key="tenant-a:analytics-copilot",
                idempotency_key="durable-request-001",
                now=self.now,
            )
        self.assertEqual(self.provider.calls, 1)

    def test_complete_execution_persists_receipts_and_final_result(self):
        execution = self.submit().execution
        outcomes = []
        for index in range(3):
            outcomes.append(
                self.runtime.run_next(
                    execution.execution_id,
                    worker_id=f"worker-{index}",
                    now=self.now + timedelta(seconds=index),
                )
            )
        self.assertEqual(outcomes[-1].outcome, "execution_completed")
        self.assertEqual(self.tools.calls, [("2026-Q1", "all"), ("2026-Q2", "all")])
        output = self.runtime.final_output(execution.execution_id)
        self.assertEqual(str(output.baseline_total_sales), "400.00")
        self.assertEqual(str(output.comparison_total_sales), "600.00")
        self.assertEqual(str(output.growth_rate_percent), "50.00")
        receipts = self.store.list_receipts(execution.execution_id)
        self.assertEqual(len(receipts), 3)
        self.assertTrue(all(receipt.status == "succeeded" for receipt in receipts))

    def test_crash_after_checkpoint_resumes_without_repeating_tool(self):
        execution = self.submit(key="durable-crash-checkpoint").execution
        crashed = self.runtime.run_next(
            execution.execution_id,
            worker_id="worker-a",
            now=self.now,
            crash_at="after_checkpoint",
        )
        self.assertEqual(crashed.outcome, "crashed")
        self.assertEqual(self.tools.calls, [("2026-Q1", "all")])
        blocked = self.runtime.run_next(
            execution.execution_id,
            worker_id="worker-b",
            now=self.now + timedelta(seconds=5),
        )
        self.assertEqual(blocked.outcome, "lease_unavailable")
        resumed = self.runtime.run_next(
            execution.execution_id,
            worker_id="worker-b",
            now=self.now + timedelta(seconds=11),
        )
        self.assertEqual(resumed.step_id, "load_comparison_sales")
        self.assertEqual(self.tools.calls.count(("2026-Q1", "all")), 1)

    def test_concurrent_takeover_obeys_lease_and_cas_state(self):
        execution = self.submit(key="durable-concurrent-takeover").execution
        crashed = self.runtime.run_next(
            execution.execution_id,
            worker_id="worker-a",
            now=self.now,
            crash_at="after_checkpoint",
        )
        self.assertEqual(crashed.state_version, 2)
        denied = self.runtime.run_next(
            execution.execution_id,
            worker_id="worker-b",
            now=self.now + timedelta(seconds=9),
        )
        self.assertEqual(denied.outcome, "lease_unavailable")
        takeover = self.runtime.run_next(
            execution.execution_id,
            worker_id="worker-b",
            now=self.now + timedelta(seconds=11),
        )
        self.assertEqual(takeover.state_version, 4)
        self.assertEqual(self.store.get_execution(execution.execution_id).state_version, 4)
        self.store.acquire_lease(
            execution.execution_id,
            worker_id="worker-c",
            now=self.now + timedelta(seconds=12),
            ttl_seconds=10,
        )
        latest = self.store.get_latest_checkpoint(execution.execution_id)
        with self.assertRaisesRegex(CheckpointConflict, "checkpoint_cas_conflict"):
            self.store.save_checkpoint(
                execution.execution_id,
                worker_id="worker-c",
                expected_state_version=2,
                execution_status="running",
                agent_state=latest.agent_state,
                now=self.now + timedelta(seconds=12),
            )

    def test_crash_after_operation_becomes_ambiguous_and_never_reexecutes(self):
        execution = self.submit(key="durable-ambiguous-outcome").execution
        crashed = self.runtime.run_next(
            execution.execution_id,
            worker_id="worker-a",
            now=self.now,
            crash_at="after_operation",
        )
        self.assertEqual(crashed.operation_calls, 1)
        self.assertEqual(len(self.tools.calls), 1)
        recovered = self.runtime.run_next(
            execution.execution_id,
            worker_id="worker-b",
            now=self.now + timedelta(seconds=11),
        )
        self.assertEqual(recovered.outcome, "reconciliation_required")
        again = self.runtime.run_next(
            execution.execution_id,
            worker_id="worker-c",
            now=self.now + timedelta(seconds=30),
        )
        self.assertEqual(again.outcome, "reconciliation_required")
        self.assertEqual(len(self.tools.calls), 1)
        receipt = self.store.list_receipts(execution.execution_id)[0]
        self.assertEqual(receipt.status, "ambiguous")
        self.assertEqual(receipt.error_code, "outcome_unknown")


if __name__ == "__main__":
    unittest.main()
