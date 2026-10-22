"""Local product path through mock DeepSeek, durable queue, Worker and PostgreSQL."""

from __future__ import annotations

from decimal import Decimal
from time import monotonic, sleep
import unittest

from pydantic import SecretStr

from backend.agent_api.auth import InternalAgentApiSettings
from backend.planning.deepseek_planner_adapter import DeepSeekPlannerSettings
from backend.planning.durable_agent_runtime import DurableAgentSettings
from backend.planning.durable_controlled_runtime import DurableControlledPlannerSettings
from backend.planning.durable_deepseek_integration import DurableDeepSeekIntegrationSettings
from backend.planning.task_queue import PersistentAgentQueueSettings
from backend.planning.worker_runtime import DurableWorkerSettings
from backend.planning.worker_supervisor import WorkerSupervisorSettings
from backend.product_agent_app import LocalAgentProductSettings, create_product_agent_app
from tests.db.test_local_agent_http import AsgiClient, TASK_BODY
from tests.test_local_agent_product import DynamicPlanOpener


KEY = "local-product-test-key-at-least-32-characters"


class LocalAgentProductDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.opener = DynamicPlanOpener()
        self.api_settings = InternalAgentApiSettings(enabled=True, api_key=KEY)
        self.app = create_product_agent_app(
            product_settings=LocalAgentProductSettings(
                enabled=True,
                max_planner_calls=3,
                planner_budget_usd=Decimal("0.02"),
                per_call_reserve_usd=Decimal("0.002"),
            ),
            api_settings=self.api_settings,
            planner_settings=DeepSeekPlannerSettings(
                enabled=True,
                api_key=SecretStr("local-product-db-fake-key-not-real"),
            ),
            durable_settings=DurableDeepSeekIntegrationSettings(
                planner=DurableControlledPlannerSettings(enabled=True, lease_ttl_seconds=1),
                worker=DurableWorkerSettings(
                    enabled=True,
                    lease_ttl_seconds=1,
                    heartbeat_interval_ms=25,
                    operation_timeout_ms=2_000,
                ),
                agent=DurableAgentSettings(enabled=True, max_steps_per_run=8),
            ),
            queue_settings=PersistentAgentQueueSettings(
                enabled=True,
                lease_ttl_seconds=1,
                poll_interval_ms=25,
            ),
            supervisor_settings=WorkerSupervisorSettings(
                enabled=True,
                max_workers=2,
                queue_capacity=4,
                poll_interval_ms=25,
                shutdown_grace_seconds=3,
            ),
            opener=self.opener,
        )
        self.runtime = self.app.state.product_agent_runtime
        self.runtime.start()
        self.runtime.supervisor.start()
        self.addCleanup(self.runtime.close)
        self.client = AsgiClient(self.app)

    def submit(self, *, key, body=None):
        return self.client.post(
            "/internal/agent/tasks",
            payload=body or TASK_BODY,
            headers={"X-Internal-Agent-Key": KEY, "Idempotency-Key": key},
        )

    def wait_completed(self, task_id):
        deadline = monotonic() + 5
        while monotonic() < deadline:
            response = self.client.get(
                f"/internal/agent/tasks/{task_id}",
                headers={"X-Internal-Agent-Key": KEY},
            )
            if response.status_code == 409:
                sleep(0.02)
                continue
            self.assertEqual(response.status_code, 200)
            if response.json()["status"] == "completed":
                return response.json()
            sleep(0.02)
        self.fail("product task did not complete")

    def test_agent_api_mock_real_adapter_worker_and_grounded_result(self):
        submitted = self.submit(key="product-flow-request-001")
        self.assertEqual(submitted.status_code, 202)
        task = submitted.json()
        self.assertEqual(task["status"], "queued")
        result = self.wait_completed(task["task_id"])
        self.assertEqual(result["result"]["baseline_total_sales"], "400.00")
        self.assertEqual(result["result"]["comparison_total_sales"], "600.00")
        self.assertEqual(result["result"]["growth_rate_percent"], "50.00")
        self.assertEqual(result["result"]["source_relation"], "analytics.sales_orders")
        self.assertEqual(result["result"]["dataset_version"], "synthetic-sales-db-v1")
        self.assertEqual(len(self.runtime.store.list_receipts(task["task_id"])), 3)
        sent = self.opener.payloads[0]
        self.assertEqual(sent["model"], "deepseek-flash")
        self.assertEqual(sent["thinking"], {"type": "disabled"})
        self.assertEqual(sent["response_format"], {"type": "json_object"})

        duplicate = self.submit(key="product-flow-request-001")
        self.assertEqual(duplicate.status_code, 202)
        self.assertFalse(duplicate.json()["created"])
        self.assertEqual(duplicate.json()["task_id"], task["task_id"])
        self.assertEqual(self.app.state.product_planner.telemetry().calls_started, 1)

    def test_second_supported_request_and_unauthorized_request(self):
        body = {**TASK_BODY, "region": "north"}
        submitted = self.submit(key="product-flow-request-002", body=body)
        self.assertEqual(submitted.status_code, 202)
        result = self.wait_completed(submitted.json()["task_id"])
        self.assertEqual(result["result"]["region"], "north")
        self.assertEqual(result["result"]["source_relation"], "analytics.sales_orders")
        telemetry = self.app.state.product_planner.telemetry()
        self.assertEqual(telemetry.calls_started, 1)
        self.assertEqual(telemetry.calls_completed, 1)
        self.assertEqual(telemetry.total_tokens, 150)

        denied = self.client.post(
            "/internal/agent/tasks",
            payload=TASK_BODY,
            headers={"Idempotency-Key": "product-denied-request-001"},
        )
        self.assertEqual(denied.status_code, 401)

        unsupported = self.client.post(
            "/internal/agent/tasks",
            payload={**TASK_BODY, "question": "Analyze any business issue"},
            headers={
                "X-Internal-Agent-Key": KEY,
                "Idempotency-Key": "product-unsupported-request-001",
            },
        )
        self.assertEqual(unsupported.status_code, 422)
        self.assertEqual(self.app.state.product_planner.telemetry().calls_started, 1)


if __name__ == "__main__":
    unittest.main()
