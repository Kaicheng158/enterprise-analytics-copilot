"""Isolated PostgreSQL proof for the internal Agent task controller."""

from contextlib import contextmanager
from datetime import UTC, datetime
import unittest
import uuid

import psycopg
from psycopg import sql

from backend.agent_api.models import AgentCaller, AgentTaskRequest
from backend.agent_api.service import DurableAgentTaskController
from backend.planning.checkpoint_store import PostgresCheckpointStore
from backend.planning.demo import build_quarter_sales_comparison_plan
from backend.planning.durable_agent_runtime import DurableAgentRuntime, DurableAgentSettings
from backend.planning.durable_controlled_runtime import (
    DurableControlledPlanner,
    DurableControlledPlannerSettings,
)
from backend.planning.worker_runtime import DurableWorkerRuntime, DurableWorkerSettings
from backend.tools.catalog import build_tool_registry
from backend.tools.executor import ToolExecutor
from scripts.migrate import apply_migrations, connect_kwargs
from scripts.seed_sales import seed_sales


class FakePlanProvider:
    def __init__(self):
        self.calls = 0

    def propose(self, request):
        self.calls += 1
        return build_quarter_sales_comparison_plan(request).model_dump_json()


class InternalAgentApiDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin_kwargs = connect_kwargs()
        cls.database_name = "eac_test_agent_api_" + uuid.uuid4().hex
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
                "TRUNCATE agent_runtime.reconciliation_events,"
                "agent_runtime.step_receipts,agent_runtime.checkpoints,"
                "agent_runtime.executions CASCADE"
            )

    def test_submit_status_execute_and_duplicate_reuse_existing_durable_path(self):
        provider = FakePlanProvider()
        store = PostgresCheckpointStore(
            self.connection_factory,
            schema="agent_runtime",
            idempotency_secret=b"internal-agent-api-test-secret-is-32-bytes",
        )
        planner = DurableControlledPlanner(
            provider,
            store,
            ToolExecutor(build_tool_registry(self.connection_factory)),
            settings=DurableControlledPlannerSettings(
                enabled=True, lease_ttl_seconds=2
            ),
        )
        worker = DurableWorkerRuntime(
            store,
            settings=DurableWorkerSettings(
                enabled=True,
                lease_ttl_seconds=2,
                heartbeat_interval_ms=50,
                operation_timeout_ms=2_000,
            ),
        )
        agent = DurableAgentRuntime(
            planner,
            worker,
            settings=DurableAgentSettings(enabled=True, max_steps_per_run=8),
        )
        controller = DurableAgentTaskController(agent, store)
        request = AgentTaskRequest(
            task="compare_quarterly_sales",
            baseline_period="2026-Q1",
            comparison_period="2026-Q2",
            region="all",
        )
        first = controller.submit(
            request,
            idempotency_key="agent-api-e2e-001",
            caller=AgentCaller(),
        )
        self.assertTrue(first.created)
        self.assertEqual(first.status, "queued")
        pending = controller.get(first.task_id, caller=AgentCaller())
        self.assertEqual((pending.status, pending.progress.completed_steps), ("queued", 0))

        result = agent.run_to_terminal(
            first.task_id,
            worker_id_prefix="agent-api-test",
        )
        self.assertEqual(result.status, "completed")
        completed = controller.get(first.task_id, caller=AgentCaller())
        self.assertEqual(completed.status, "completed")
        self.assertEqual(str(completed.result.baseline_total_sales), "400.00")
        self.assertEqual(str(completed.result.comparison_total_sales), "600.00")
        self.assertEqual(str(completed.result.growth_rate_percent), "50.00")
        self.assertEqual(completed.progress.completed_steps, 3)

        duplicate = controller.submit(
            request,
            idempotency_key="agent-api-e2e-001",
            caller=AgentCaller(),
        )
        self.assertFalse(duplicate.created)
        self.assertEqual(duplicate.task_id, first.task_id)
        self.assertEqual(provider.calls, 1)
        receipts = store.list_receipts(first.task_id)
        self.assertEqual(len(receipts), 3)
        self.assertTrue(all(item.status == "succeeded" for item in receipts))


if __name__ == "__main__":
    unittest.main()
