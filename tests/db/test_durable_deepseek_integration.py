"""Mock-DeepSeek to durable PostgreSQL execution in a disposable database."""

from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
import json
from time import sleep
import unittest
import uuid

import psycopg
from psycopg import sql
from pydantic import SecretStr

from backend.planning.checkpoint_store import PostgresCheckpointStore
from backend.planning.deepseek_planner_adapter import (
    DeepSeekPlannerSettings,
    DeepSeekStructuredPlannerTransport,
)
from backend.planning.demo import build_quarter_sales_comparison_plan
from backend.planning.durable_agent_runtime import DurableAgentSettings
from backend.planning.durable_controlled_runtime import DurableControlledPlannerSettings
from backend.planning.durable_deepseek_integration import (
    DurableDeepSeekIntegrationSettings,
    build_durable_deepseek_agent_runtime,
)
from backend.planning.models import PlanningRequest
from backend.planning.planner_safety import PlannerCostGuard
from backend.planning.structured_provider import PLANNER_PROMPT_SHA256
from backend.planning.worker_runtime import DurableWorkerSettings
from scripts.migrate import apply_migrations, connect_kwargs
from scripts.seed_sales import seed_sales


SCHEMA = "agent_runtime"


class FakeHttpResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return self._payload[:limit]


class CapturingDeepSeekOpener:
    def __init__(self, plan_json, *, allow_calls=True):
        self.plan_json = plan_json
        self.allow_calls = allow_calls
        self.calls = []

    def __call__(self, request, timeout):
        if not self.allow_calls:
            raise AssertionError("recovery must not ask the Planner again")
        self.calls.append(
            {
                "url": request.full_url,
                "payload": json.loads(request.data),
                "timeout": timeout,
            }
        )
        return FakeHttpResponse(
            {
                "id": "phase56-db-mock-plan",
                "model": "deepseek-flash",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": self.plan_json,
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 500,
                    "completion_tokens": 300,
                    "total_tokens": 800,
                },
            }
        )


class DurableDeepSeekIntegrationDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin_kwargs = connect_kwargs()
        cls.database_name = "eac_test_phase56_" + uuid.uuid4().hex
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
            idempotency_secret=b"phase56-integration-secret-is-at-least-32-bytes",
        )

    @staticmethod
    def integration_settings():
        return DurableDeepSeekIntegrationSettings(
            planner=DurableControlledPlannerSettings(
                enabled=True, lease_ttl_seconds=1
            ),
            worker=DurableWorkerSettings(
                enabled=True,
                lease_ttl_seconds=1,
                heartbeat_interval_ms=50,
                operation_timeout_ms=2_000,
            ),
            agent=DurableAgentSettings(enabled=True, max_steps_per_run=8),
        )

    def new_stack(self, *, opener, store=None):
        guard = PlannerCostGuard(max_calls=1)
        transport = DeepSeekStructuredPlannerTransport(
            DeepSeekPlannerSettings(
                enabled=True,
                api_key=SecretStr("phase56-db-test-key-not-real"),
            ),
            cost_guard=guard,
            opener=opener,
        )
        agent, provider = build_durable_deepseek_agent_runtime(
            transport,
            store or self.new_store(),
            settings=self.integration_settings(),
            connection_factory=self.connection_factory,
        )
        return agent, provider, transport, guard

    @staticmethod
    def submit(agent, key):
        return agent.submit(
            PlanningRequest(),
            scope_key="tenant-a:analytics-copilot",
            idempotency_key=key,
            now=datetime.now(UTC),
        )

    def assert_expected_answer(self, output):
        self.assertEqual(output.baseline_total_sales, Decimal("400.00"))
        self.assertEqual(output.comparison_total_sales, Decimal("600.00"))
        self.assertEqual(output.absolute_change, Decimal("200.00"))
        self.assertEqual(output.growth_rate_percent, Decimal("50.00"))
        self.assertEqual(output.source_relation, "analytics.sales_orders")
        self.assertEqual(output.dataset_version, "synthetic-sales-db-v1")

    def test_mock_deepseek_plan_runs_durably_and_duplicate_request_is_idempotent(self):
        request = PlanningRequest()
        opener = CapturingDeepSeekOpener(
            build_quarter_sales_comparison_plan(request).model_dump_json()
        )
        store = self.new_store()
        agent, provider, transport, guard = self.new_stack(
            opener=opener, store=store
        )

        first = self.submit(agent, "phase56-complete-request")
        duplicate = self.submit(agent, "phase56-complete-request")
        result = agent.run_to_terminal(
            first.execution.execution_id, worker_id_prefix="phase56-worker"
        )

        self.assertTrue(first.created)
        self.assertFalse(duplicate.created)
        self.assertEqual(first.execution.execution_id, duplicate.execution.execution_id)
        self.assertEqual(result.status, "completed")
        self.assert_expected_answer(result.output)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(transport.calls, 1)
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(guard.calls_started, 1)
        self.assertEqual(guard.spent_usd, Decimal("0.00051"))
        self.assertEqual(provider.last_request.prompt_sha256, PLANNER_PROMPT_SHA256)
        self.assertEqual(provider.last_response.usage.total_tokens, 800)
        sent = opener.calls[0]["payload"]
        self.assertEqual(sent["model"], "deepseek-flash")
        self.assertEqual(sent["response_format"], {"type": "json_object"})
        self.assertEqual(sent["thinking"], {"type": "disabled"})
        receipts = store.list_receipts(first.execution.execution_id)
        self.assertEqual(len(receipts), 3)
        self.assertTrue(all(item.status == "succeeded" for item in receipts))
        self.assertEqual(
            store.audit_execution_consistency(first.execution.execution_id).completed_receipt_count,
            3,
        )

    def test_restart_after_committed_checkpoint_does_not_regenerate_or_replay(self):
        request = PlanningRequest()
        opener = CapturingDeepSeekOpener(
            build_quarter_sales_comparison_plan(request).model_dump_json()
        )
        store = self.new_store()
        agent, _, _, _ = self.new_stack(opener=opener, store=store)
        creation = self.submit(agent, "phase56-restart-request")
        execution_id = creation.execution.execution_id
        crashed = agent.run_next(
            execution_id,
            worker_id="phase56-crash-worker",
            crash_at="after_checkpoint",
        )
        self.assertEqual(crashed.outcome, "crashed")
        self.assertEqual(len(store.list_receipts(execution_id)), 1)
        sleep(1.05)

        recovery_opener = CapturingDeepSeekOpener("", allow_calls=False)
        restarted_store = self.new_store()
        restarted, provider, transport, guard = self.new_stack(
            opener=recovery_opener,
            store=restarted_store,
        )
        result = restarted.run_to_terminal(
            execution_id, worker_id_prefix="phase56-recovery-worker"
        )

        self.assertEqual(result.status, "completed")
        self.assert_expected_answer(result.output)
        self.assertEqual(provider.calls, 0)
        self.assertEqual(transport.calls, 0)
        self.assertEqual(guard.calls_started, 0)
        self.assertEqual(recovery_opener.calls, [])
        receipts = restarted_store.list_receipts(execution_id)
        self.assertEqual(len(receipts), 3)
        self.assertEqual(
            [item.step_id for item in receipts],
            ["load_baseline_sales", "load_comparison_sales", "calculate_growth"],
        )


if __name__ == "__main__":
    unittest.main()
