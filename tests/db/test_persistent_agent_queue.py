"""Phase 5.7c queue/Worker/HTTP tests in disposable PostgreSQL databases."""

from __future__ import annotations

from datetime import UTC, datetime
from threading import Event, Lock, Thread
from time import monotonic, sleep
import unittest

from backend.agent_api.api import create_internal_agent_app
from backend.agent_api.auth import InternalAgentApiSettings
from backend.agent_api.local_runtime import (
    DeterministicLocalPlanProvider,
    DisposableQueuedAgentRuntime,
    LocalAgentServiceSettings,
)
from backend.planning.models import PlanningRequest
from backend.planning.task_queue import PersistentAgentQueueSettings
from tests.db.test_local_agent_http import AsgiClient, TASK_BODY


KEY = "persistent-agent-queue-test-key-at-least-32-characters"


class SlowExecutor:
    def __init__(self, delegate, *, delay_seconds=0.25):
        self._delegate = delegate
        self._delay_seconds = delay_seconds
        self.calls = 0

    def execute(self, call, context):
        self.calls += 1
        sleep(self._delay_seconds)
        return self._delegate.execute(call, context)


class PersistentAgentQueueDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.provider = DeterministicLocalPlanProvider()
        cls.api_settings = InternalAgentApiSettings(enabled=True, api_key=KEY)
        cls.queue_settings = PersistentAgentQueueSettings(
            enabled=True,
            lease_ttl_seconds=1,
            poll_interval_ms=25,
            max_tasks_per_drain=10,
        )
        cls.runtime = DisposableQueuedAgentRuntime(
            settings=LocalAgentServiceSettings(
                enabled=True,
                lease_ttl_seconds=1,
                heartbeat_interval_ms=50,
                operation_timeout_ms=2_000,
            ),
            api_settings=cls.api_settings,
            queue_settings=cls.queue_settings,
            provider=cls.provider,
        )
        cls.runtime.start()
        cls.addClassCleanup(cls.runtime.close)
        cls.app = create_internal_agent_app(
            settings=cls.api_settings,
            controller=cls.runtime,
            require_loopback=True,
        )

    def setUp(self):
        with self.runtime.connection_factory() as database:
            database.execute(
                "TRUNCATE agent_runtime.reconciliation_events,"
                "agent_runtime.step_receipts,agent_runtime.checkpoints,"
                "agent_runtime.executions CASCADE"
            )
            database.commit()
        self.provider.calls = 0
        self.client = AsgiClient(self.app)
        self.headers = {
            "X-Internal-Agent-Key": KEY,
            "Idempotency-Key": "queue-http-request-001",
        }

    def submit(self, *, key="queue-http-request-001", body=None):
        return self.client.post(
            "/internal/agent/tasks",
            payload=body or TASK_BODY,
            headers={"X-Internal-Agent-Key": KEY, "Idempotency-Key": key},
        )

    def get(self, task_id):
        return self.client.get(
            f"/internal/agent/tasks/{task_id}",
            headers={"X-Internal-Agent-Key": KEY},
        )

    def test_http_submission_is_persisted_before_independent_worker_runs(self):
        submitted = self.submit()
        self.assertEqual(submitted.status_code, 202)
        accepted = submitted.json()
        self.assertEqual(accepted["status"], "queued")
        self.assertEqual(self.get(accepted["task_id"]).json()["status"], "queued")
        self.assertEqual(self.runtime.store.list_receipts(accepted["task_id"]), ())

        worked = self.runtime.queue_worker.run_once(worker_id="worker-one")
        self.assertEqual(worked.outcome, "completed")
        self.assertEqual(str(worked.execution_id), accepted["task_id"])
        completed = self.get(accepted["task_id"])
        self.assertEqual(completed.status_code, 200)
        body = completed.json()
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["result"]["growth_rate_percent"], "50.00")
        self.assertEqual(len(self.runtime.store.list_receipts(accepted["task_id"])), 3)

    def test_polling_worker_thread_updates_real_http_status(self):
        stop = Event()
        worker = Thread(
            target=self.runtime.queue_worker.run_forever,
            kwargs={"worker_id": "poll-worker", "stop_event": stop},
        )
        worker.start()
        try:
            accepted = self.submit(key="queue-polling-request-001").json()
            deadline = monotonic() + 3
            observed = None
            while monotonic() < deadline:
                response = self.get(accepted["task_id"])
                if response.status_code == 409:
                    self.assertEqual(
                        response.json()["detail"]["code"], "agent_status_changed"
                    )
                    sleep(0.02)
                    continue
                self.assertEqual(response.status_code, 200)
                observed = response.json()
                if observed["status"] == "completed":
                    break
                sleep(0.02)
            self.assertIsNotNone(observed)
            self.assertEqual(observed["status"], "completed")
            self.assertEqual(observed["result"]["absolute_change"], "200.00")
        finally:
            stop.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())

    def test_duplicate_request_and_concurrent_claim_do_not_repeat_execution(self):
        first = self.submit(key="queue-duplicate-request-001").json()
        duplicate = self.submit(key="queue-duplicate-request-001").json()
        self.assertTrue(first["created"])
        self.assertFalse(duplicate["created"])
        self.assertEqual(first["task_id"], duplicate["task_id"])
        self.assertEqual(self.provider.calls, 1)

        results = []
        lock = Lock()

        def work(worker_id):
            result = self.runtime.queue_worker.run_once(worker_id=worker_id)
            with lock:
                results.append(result)

        one = Thread(target=work, args=("concurrent-one",))
        two = Thread(target=work, args=("concurrent-two",))
        one.start()
        two.start()
        one.join(5)
        two.join(5)
        self.assertFalse(one.is_alive())
        self.assertFalse(two.is_alive())
        self.assertEqual({item.outcome for item in results}, {"completed", "idle"})
        self.assertEqual(len(self.runtime.store.list_receipts(first["task_id"])), 3)

    def test_expired_claim_before_step_is_safely_taken_over(self):
        accepted = self.submit(key="queue-before-step-crash-001").json()
        owner = f"{self.runtime.queue_worker.worker_prefix('crashed-worker')}-0"
        claimed = self.runtime.store.claim_next_execution(
            scope_key="internal-agent:analytics-copilot",
            worker_id=owner,
            now=datetime.now(UTC),
            ttl_seconds=1,
        )
        self.assertEqual(str(claimed.execution_id), accepted["task_id"])
        self.assertEqual(self.runtime.store.list_receipts(claimed.execution_id), ())
        sleep(1.05)

        recovered = self.runtime.queue_worker.run_once(worker_id="takeover-worker")
        self.assertEqual(recovered.outcome, "completed")
        self.assertEqual(self.get(accepted["task_id"]).json()["status"], "completed")

    def test_crash_after_step_start_stops_in_reconciliation_without_replay(self):
        accepted = self.submit(key="queue-started-crash-001").json()
        execution_id = accepted["task_id"]
        prefix = self.runtime.queue_worker.worker_prefix("started-crash")
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
        self.assertEqual(len(self.runtime.store.list_receipts(claimed.execution_id)), 1)
        sleep(1.05)

        recovered = self.runtime.queue_worker.run_once(worker_id="recovery-worker")
        self.assertEqual(recovered.outcome, "reconciling")
        status = self.get(execution_id).json()
        self.assertEqual(status["status"], "needs_reconciliation")
        receipts = self.runtime.store.list_receipts(claimed.execution_id)
        self.assertEqual([(item.step_id, item.status) for item in receipts], [
            ("load_baseline_sales", "ambiguous")
        ])

    def test_auth_failure_never_enqueues_and_scope_filter_skips_other_tasks(self):
        unauthorized = self.client.post(
            "/internal/agent/tasks",
            payload=TASK_BODY,
            headers={"Idempotency-Key": "queue-unauthorized-001"},
        )
        self.assertEqual(unauthorized.status_code, 401)
        other = self.runtime.agent.submit(
            request=PlanningRequest(),
            scope_key="other-scope",
            idempotency_key="queue-other-scope-001",
            now=datetime.now(UTC),
        )
        idle = self.runtime.queue_worker.run_once(worker_id="scope-worker")
        self.assertEqual(idle.outcome, "idle")
        self.assertEqual(
            self.runtime.store.get_execution(other.execution.execution_id).status,
            "pending",
        )

    def test_operation_timeout_enters_reconciliation_and_is_not_retried(self):
        slow_holder = {}

        def wrap(delegate):
            slow_holder["executor"] = SlowExecutor(delegate)
            return slow_holder["executor"]

        runtime = DisposableQueuedAgentRuntime(
            settings=LocalAgentServiceSettings(
                enabled=True,
                lease_ttl_seconds=1,
                heartbeat_interval_ms=25,
                operation_timeout_ms=100,
            ),
            api_settings=self.api_settings,
            queue_settings=self.queue_settings,
            provider=DeterministicLocalPlanProvider(),
            tool_executor_wrapper=wrap,
        )
        runtime.start()
        try:
            app = create_internal_agent_app(
                settings=self.api_settings,
                controller=runtime,
                require_loopback=True,
            )
            client = AsgiClient(app)
            submitted = client.post(
                "/internal/agent/tasks",
                payload=TASK_BODY,
                headers={
                    "X-Internal-Agent-Key": KEY,
                    "Idempotency-Key": "queue-timeout-request-001",
                },
            ).json()
            timed_out = runtime.queue_worker.run_once(worker_id="timeout-worker")
            self.assertEqual(timed_out.outcome, "reconciling")
            sleep(0.3)
            status = client.get(
                f"/internal/agent/tasks/{submitted['task_id']}",
                headers={"X-Internal-Agent-Key": KEY},
            ).json()
            self.assertEqual(status["status"], "needs_reconciliation")
            self.assertEqual(slow_holder["executor"].calls, 1)
            self.assertEqual(
                runtime.queue_worker.run_once(worker_id="timeout-retry").outcome,
                "idle",
            )
        finally:
            runtime.close()


if __name__ == "__main__":
    unittest.main()
