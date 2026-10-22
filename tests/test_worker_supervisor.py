"""Offline contracts for bounded Agent Worker lifecycle management."""

from __future__ import annotations

import os
from threading import Event, Lock, Thread
from time import monotonic, sleep
import unittest
from unittest.mock import patch
from uuid import uuid4

from backend.managed_agent_app import create_managed_agent_app
from backend.planning.task_queue import QueueWorkResult
from backend.planning.worker_supervisor import (
    PersistentAgentWorkerSupervisor,
    WorkerSupervisorSettings,
    load_worker_supervisor_settings,
)


class FakeStore:
    def queue_status_counts(self, *, scope_key):
        return {"pending": 0, "running": 0, "reconciling": 0, "total": 0}


class BlockingQueueWorker:
    def __init__(self, task_count=2):
        self._lock = Lock()
        self._remaining = task_count
        self.active = 0
        self.peak = 0
        self.entered = Event()
        self.release = Event()

    def run_once(self, *, worker_id, on_claimed=None):
        with self._lock:
            if self._remaining == 0:
                return QueueWorkResult(outcome="idle")
            self._remaining -= 1
            self.active += 1
            self.peak = max(self.peak, self.active)
            if self.active == 2:
                self.entered.set()
        execution_id = uuid4()
        if on_claimed is not None:
            on_claimed(execution_id)
        self.release.wait(2)
        with self._lock:
            self.active -= 1
        return QueueWorkResult(outcome="completed", execution_id=execution_id)


class WorkerSupervisorOfflineTests(unittest.TestCase):
    def test_settings_default_off_and_strict_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = load_worker_supervisor_settings()
        self.assertFalse(settings.enabled)
        self.assertEqual(settings.max_workers, 2)
        self.assertEqual(settings.queue_capacity, 100)
        with patch.dict(
            os.environ,
            {"AGENT_WORKER_SUPERVISOR_ENABLED": "sometimes"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "configuration"):
                load_worker_supervisor_settings()

    def test_default_managed_app_is_disabled_without_database_side_effect(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "backend.agent_api.auth.load_dotenv"
        ):
            app = create_managed_agent_app()
        runtime = app.state.managed_agent_runtime
        self.assertFalse(runtime.supervisor_settings.enabled)
        self.assertIsNone(runtime.database_name)

    def test_disabled_supervisor_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "worker_supervisor_disabled"):
            PersistentAgentWorkerSupervisor(
                None,
                None,
                scope_key="internal-agent:analytics-copilot",
                settings=WorkerSupervisorSettings(enabled=False),
            )

    def test_bounded_concurrency_and_graceful_shutdown(self):
        queue_worker = BlockingQueueWorker(task_count=2)
        supervisor = PersistentAgentWorkerSupervisor(
            queue_worker,
            FakeStore(),
            scope_key="internal-agent:analytics-copilot",
            settings=WorkerSupervisorSettings(
                enabled=True,
                max_workers=2,
                poll_interval_ms=25,
                shutdown_grace_seconds=1,
            ),
        )
        supervisor.start()
        self.assertTrue(queue_worker.entered.wait(1))
        deadline = monotonic() + 1
        while monotonic() < deadline:
            if supervisor.metrics().active_workers == 2:
                break
            sleep(0.01)
        self.assertEqual(supervisor.metrics().active_workers, 2)
        results = []
        stopper = Thread(target=lambda: results.append(supervisor.stop()))
        stopper.start()
        deadline = monotonic() + 1
        while monotonic() < deadline:
            if supervisor.metrics().state == "stopping":
                break
            sleep(0.01)
        self.assertEqual(supervisor.metrics().state, "stopping")
        queue_worker.release.set()
        stopper.join(2)
        self.assertFalse(stopper.is_alive())
        self.assertTrue(results[0].stopped)
        metrics = supervisor.metrics()
        self.assertEqual(metrics.state, "stopped")
        self.assertEqual(metrics.tasks_claimed, 2)
        self.assertEqual(metrics.tasks_completed, 2)
        self.assertEqual(metrics.peak_active_workers, 2)
        self.assertEqual(metrics.active_workers, 0)

    def test_shutdown_timeout_is_visible_and_does_not_force_kill(self):
        queue_worker = BlockingQueueWorker(task_count=1)
        supervisor = PersistentAgentWorkerSupervisor(
            queue_worker,
            FakeStore(),
            scope_key="internal-agent:analytics-copilot",
            settings=WorkerSupervisorSettings(
                enabled=True,
                max_workers=1,
                poll_interval_ms=25,
                shutdown_grace_seconds=0.1,
            ),
        )
        supervisor.start()
        deadline = monotonic() + 1
        while monotonic() < deadline and queue_worker.active != 1:
            sleep(0.01)
        first = supervisor.stop()
        self.assertTrue(first.timed_out)
        self.assertEqual(supervisor.metrics().shutdown_timeouts, 1)
        queue_worker.release.set()
        second = supervisor.stop(grace_seconds=1)
        self.assertTrue(second.stopped)


if __name__ == "__main__":
    unittest.main()
