"""Phase 5.7d Worker supervisor and queue backpressure integration tests."""

from __future__ import annotations

from datetime import UTC, datetime
from threading import Event, Lock, Thread
from time import monotonic, sleep
import unittest

from backend.agent_api.api import create_internal_agent_app
from backend.agent_api.auth import InternalAgentApiSettings
from backend.agent_api.local_runtime import (
    DeterministicLocalPlanProvider,
    DisposableManagedAgentRuntime,
    LocalAgentServiceSettings,
)
from backend.planning.task_queue import PersistentAgentQueueSettings
from backend.planning.worker_supervisor import WorkerSupervisorSettings
from tests.db.test_local_agent_http import AsgiClient, TASK_BODY


KEY = "worker-supervisor-test-key-at-least-32-characters"


class ConcurrentSlowExecutor:
    def __init__(self, delegate, *, delay_seconds=0.12):
        self._delegate = delegate
        self._delay_seconds = delay_seconds
        self._lock = Lock()
        self.calls = 0
        self.active = 0
        self.peak = 0
        self.two_active = Event()

    def execute(self, call, context):
        with self._lock:
            self.calls += 1
            self.active += 1
            self.peak = max(self.peak, self.active)
            if self.active >= 2:
                self.two_active.set()
        try:
            sleep(self._delay_seconds)
            return self._delegate.execute(call, context)
        finally:
            with self._lock:
                self.active -= 1


class WorkerSupervisorDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.executor_holder = {}

        def wrap(delegate):
            wrapped = ConcurrentSlowExecutor(delegate)
            cls.executor_holder["executor"] = wrapped
            return wrapped

        cls.api_settings = InternalAgentApiSettings(enabled=True, api_key=KEY)
        cls.queue_settings = PersistentAgentQueueSettings(
            enabled=True,
            lease_ttl_seconds=1,
            poll_interval_ms=25,
            max_tasks_per_drain=10,
        )
        cls.supervisor_settings = WorkerSupervisorSettings(
            enabled=True,
            max_workers=2,
            queue_capacity=4,
            poll_interval_ms=25,
            shutdown_grace_seconds=3,
        )
        cls.runtime = DisposableManagedAgentRuntime(
            settings=LocalAgentServiceSettings(
                enabled=True,
                lease_ttl_seconds=1,
                heartbeat_interval_ms=25,
                operation_timeout_ms=2_000,
                max_active_executions=4,
            ),
            api_settings=cls.api_settings,
            queue_settings=cls.queue_settings,
            supervisor_settings=cls.supervisor_settings,
            provider=DeterministicLocalPlanProvider(),
            tool_executor_wrapper=wrap,
        )
        cls.runtime.start()
        cls.addClassCleanup(cls.runtime.close)
        cls.app = create_internal_agent_app(
            settings=cls.api_settings,
            controller=cls.runtime,
            require_loopback=True,
        )

    def setUp(self):
        self.runtime.supervisor.stop(grace_seconds=3)
        with self.runtime.connection_factory() as database:
            database.execute(
                "TRUNCATE agent_runtime.reconciliation_events,"
                "agent_runtime.step_receipts,agent_runtime.checkpoints,"
                "agent_runtime.executions CASCADE"
            )
            database.commit()
        self.client = AsgiClient(self.app)

    def submit(self, key):
        return self.client.post(
            "/internal/agent/tasks",
            payload=TASK_BODY,
            headers={"X-Internal-Agent-Key": KEY, "Idempotency-Key": key},
        )

    def status(self, task_id):
        return self.client.get(
            f"/internal/agent/tasks/{task_id}",
            headers={"X-Internal-Agent-Key": KEY},
        )

    def wait_status(self, task_id, expected, *, timeout=5):
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            response = self.status(task_id)
            if response.status_code == 409:
                sleep(0.02)
                continue
            self.assertEqual(response.status_code, 200)
            if response.json()["status"] == expected:
                return response.json()
            sleep(0.02)
        self.fail(f"task {task_id} did not reach {expected}")

    def test_bounded_concurrency_graceful_stop_and_restart_preserve_tasks(self):
        accepted = [
            self.submit(f"supervisor-graceful-{index:03d}").json()
            for index in range(4)
        ]
        self.runtime.supervisor.start()
        executor = self.executor_holder["executor"]
        self.assertTrue(executor.two_active.wait(2))
        stopped = self.runtime.supervisor.stop(grace_seconds=3)
        self.assertTrue(stopped.stopped)
        metrics = self.runtime.supervisor.metrics()
        self.assertLessEqual(metrics.peak_active_workers, 2)
        self.assertEqual(metrics.active_workers, 0)
        statuses = [self.status(item["task_id"]).json()["status"] for item in accepted]
        self.assertEqual(statuses.count("completed"), 2)
        self.assertEqual(statuses.count("queued"), 2)

        self.runtime.supervisor.start()
        for item in accepted:
            completed = self.wait_status(item["task_id"], "completed")
            self.assertEqual(completed["result"]["growth_rate_percent"], "50.00")
        self.assertTrue(self.runtime.supervisor.stop(grace_seconds=3).stopped)
        self.assertEqual(
            sum(len(self.runtime.store.list_receipts(item["task_id"])) for item in accepted),
            12,
        )

    def test_capacity_is_atomic_and_duplicate_is_allowed_when_full(self):
        runtime = DisposableManagedAgentRuntime(
            settings=LocalAgentServiceSettings(
                enabled=True,
                lease_ttl_seconds=1,
                heartbeat_interval_ms=25,
                operation_timeout_ms=2_000,
                max_active_executions=2,
            ),
            api_settings=self.api_settings,
            queue_settings=self.queue_settings,
            supervisor_settings=self.supervisor_settings.model_copy(
                update={"queue_capacity": 2}
            ),
            provider=DeterministicLocalPlanProvider(),
        )
        runtime.start()
        try:
            app = create_internal_agent_app(
                settings=self.api_settings,
                controller=runtime,
                require_loopback=True,
            )
            client = AsgiClient(app)
            responses = []
            lock = Lock()

            def submit(index):
                response = client.post(
                    "/internal/agent/tasks",
                    payload=TASK_BODY,
                    headers={
                        "X-Internal-Agent-Key": KEY,
                        "Idempotency-Key": f"capacity-request-{index:03d}",
                    },
                )
                with lock:
                    responses.append((f"capacity-request-{index:03d}", response))

            threads = [Thread(target=submit, args=(index,)) for index in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(
                sorted(item.status_code for _key, item in responses),
                [202, 202, 429, 429],
            )
            accepted_key, accepted_response = next(
                (key, item) for key, item in responses if item.status_code == 202
            )
            accepted = accepted_response.json()
            duplicate = client.post(
                "/internal/agent/tasks",
                payload=TASK_BODY,
                headers={
                    "X-Internal-Agent-Key": KEY,
                    "Idempotency-Key": accepted_key,
                },
            )
            self.assertEqual(duplicate.status_code, 202)
            self.assertFalse(duplicate.json()["created"])
            self.assertEqual(
                runtime.store.queue_status_counts(
                    scope_key="internal-agent:analytics-copilot"
                )["total"],
                2,
            )
        finally:
            runtime.close()

    def test_unknown_effect_is_reconciled_without_automatic_retry(self):
        accepted = self.submit("supervisor-ambiguous-001").json()
        prefix = self.runtime.queue_worker.worker_prefix("interrupted-worker")
        claimed = self.runtime.store.claim_next_execution(
            scope_key="internal-agent:analytics-copilot",
            worker_id=f"{prefix}-0",
            now=datetime.now(UTC),
            ttl_seconds=1,
        )
        crashed = self.runtime.agent.run_next(
            claimed.execution_id,
            worker_id=f"{prefix}-0",
            crash_at="after_start",
        )
        self.assertEqual(crashed.outcome, "crashed")
        calls_before = self.executor_holder["executor"].calls
        sleep(1.05)
        self.runtime.supervisor.start()
        status = self.wait_status(accepted["task_id"], "needs_reconciliation")
        self.assertEqual(status["error"]["code"], "agent_reconciliation_required")
        self.assertTrue(self.runtime.supervisor.stop(grace_seconds=3).stopped)
        self.assertEqual(self.executor_holder["executor"].calls, calls_before)
        receipts = self.runtime.store.list_receipts(claimed.execution_id)
        self.assertEqual([(item.step_id, item.status) for item in receipts], [
            ("load_baseline_sales", "ambiguous")
        ])


if __name__ == "__main__":
    unittest.main()
