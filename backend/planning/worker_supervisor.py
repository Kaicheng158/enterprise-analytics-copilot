"""Default-off lifecycle supervisor for bounded persistent Agent workers."""

from __future__ import annotations

import logging
import os
from threading import Event, Lock, Thread
from time import monotonic
from typing import Literal

from pydantic import Field

from backend.tools.models import StrictModel

from .checkpoint_store import PostgresCheckpointStore
from .task_queue import PersistentAgentQueueWorker, QueueWorkResult


LOGGER = logging.getLogger("uvicorn.error")


class WorkerSupervisorSettings(StrictModel):
    enabled: bool = False
    max_workers: int = Field(default=2, ge=1, le=8)
    queue_capacity: int = Field(default=100, ge=1, le=10_000)
    poll_interval_ms: int = Field(default=100, ge=25, le=60_000)
    shutdown_grace_seconds: float = Field(default=5.0, ge=0.1, le=60.0)


class WorkerSupervisorMetrics(StrictModel):
    state: Literal["created", "running", "stopping", "stopped"]
    configured_workers: int
    alive_workers: int
    active_workers: int
    peak_active_workers: int
    tasks_claimed: int
    tasks_completed: int
    tasks_reconciling: int
    tasks_paused: int
    tasks_failed: int
    tasks_incomplete: int
    worker_errors: int
    idle_polls: int
    poll_errors: int
    shutdown_timeouts: int
    queue_pending: int
    queue_running: int
    queue_reconciling: int
    queue_total: int


class WorkerShutdownResult(StrictModel):
    stopped: bool
    timed_out: bool
    alive_workers: int
    elapsed_seconds: float = Field(ge=0)


def _parse_bool(name: str, default: str = "false") -> bool:
    raw = os.getenv(name, default).strip().lower()
    values = {"true": True, "false": False, "1": True, "0": False}
    if raw not in values:
        raise ValueError(f"Invalid {name} value")
    return values[raw]


def load_worker_supervisor_settings() -> WorkerSupervisorSettings:
    try:
        return WorkerSupervisorSettings(
            enabled=_parse_bool("AGENT_WORKER_SUPERVISOR_ENABLED"),
            max_workers=int(os.getenv("AGENT_WORKER_MAX_CONCURRENCY", "2")),
            queue_capacity=int(os.getenv("AGENT_QUEUE_CAPACITY", "100")),
            poll_interval_ms=int(os.getenv("AGENT_WORKER_POLL_INTERVAL_MS", "100")),
            shutdown_grace_seconds=float(
                os.getenv("AGENT_WORKER_SHUTDOWN_GRACE_SECONDS", "5")
            ),
        )
    except ValueError as error:
        raise ValueError("Invalid Agent Worker supervisor configuration") from error


class PersistentAgentWorkerSupervisor:
    """Run a bounded number of existing queue workers with cooperative shutdown."""

    def __init__(
        self,
        queue_worker: PersistentAgentQueueWorker,
        store: PostgresCheckpointStore,
        *,
        scope_key: str,
        settings: WorkerSupervisorSettings | None = None,
        worker_id_prefix: str = "managed-worker",
    ):
        self.settings = settings or load_worker_supervisor_settings()
        if not self.settings.enabled:
            raise ValueError("worker_supervisor_disabled")
        if not 1 <= len(scope_key) <= 160:
            raise ValueError("invalid worker supervisor scope")
        PersistentAgentQueueWorker.worker_prefix(worker_id_prefix)
        self._queue_worker = queue_worker
        self._store = store
        self._scope_key = scope_key
        self._worker_id_prefix = worker_id_prefix
        self._stop_event = Event()
        self._lock = Lock()
        self._threads: list[Thread] = []
        self._state = "created"
        self._counts = {
            "active_workers": 0,
            "peak_active_workers": 0,
            "tasks_claimed": 0,
            "tasks_completed": 0,
            "tasks_reconciling": 0,
            "tasks_paused": 0,
            "tasks_failed": 0,
            "tasks_incomplete": 0,
            "worker_errors": 0,
            "idle_polls": 0,
            "poll_errors": 0,
            "shutdown_timeouts": 0,
        }

    def start(self) -> None:
        with self._lock:
            if self._state == "running":
                return
            if any(thread.is_alive() for thread in self._threads):
                raise RuntimeError("worker_supervisor_still_stopping")
            self._stop_event = Event()
            self._threads = [
                Thread(
                    target=self._worker_loop,
                    args=(f"{self._worker_id_prefix}-{index}",),
                    name=f"agent-worker-{index}",
                    daemon=True,
                )
                for index in range(self.settings.max_workers)
            ]
            self._state = "running"
            threads = tuple(self._threads)
        for thread in threads:
            thread.start()

    def stop(self, *, grace_seconds: float | None = None) -> WorkerShutdownResult:
        grace = (
            self.settings.shutdown_grace_seconds
            if grace_seconds is None
            else grace_seconds
        )
        if not 0.1 <= grace <= 60:
            raise ValueError("invalid shutdown grace period")
        started = monotonic()
        with self._lock:
            threads = tuple(self._threads)
            if self._state != "stopped":
                self._state = "stopping"
            self._stop_event.set()
        deadline = started + grace
        for thread in threads:
            thread.join(max(0.0, deadline - monotonic()))
        alive = sum(thread.is_alive() for thread in threads)
        timed_out = alive > 0
        with self._lock:
            if timed_out:
                self._counts["shutdown_timeouts"] += 1
            else:
                self._state = "stopped"
        return WorkerShutdownResult(
            stopped=not timed_out,
            timed_out=timed_out,
            alive_workers=alive,
            elapsed_seconds=max(0.0, monotonic() - started),
        )

    def metrics(self) -> WorkerSupervisorMetrics:
        with self._lock:
            values = dict(self._counts)
            state = self._state
            alive = sum(thread.is_alive() for thread in self._threads)
        queue = self._store.queue_status_counts(scope_key=self._scope_key)
        return WorkerSupervisorMetrics(
            state=state,
            configured_workers=self.settings.max_workers,
            alive_workers=alive,
            queue_pending=queue["pending"],
            queue_running=queue["running"],
            queue_reconciling=queue["reconciling"],
            queue_total=queue["total"],
            **values,
        )

    def _worker_loop(self, worker_id: str) -> None:
        interval = self.settings.poll_interval_ms / 1_000
        while not self._stop_event.is_set():
            claimed = False

            def observed_claim(_execution_id) -> None:
                nonlocal claimed
                claimed = True
                with self._lock:
                    self._counts["active_workers"] += 1
                    self._counts["tasks_claimed"] += 1
                    self._counts["peak_active_workers"] = max(
                        self._counts["peak_active_workers"],
                        self._counts["active_workers"],
                    )

            try:
                result = self._queue_worker.run_once(
                    worker_id=worker_id,
                    on_claimed=observed_claim,
                )
            except Exception:
                with self._lock:
                    self._counts["poll_errors"] += 1
                LOGGER.info("agent_worker_supervisor %s", {"status": "poll_error"})
                self._stop_event.wait(interval)
                continue
            finally:
                if claimed:
                    with self._lock:
                        self._counts["active_workers"] -= 1
            self._record_result(result)
            if result.outcome in {"idle", "worker_error", "paused", "incomplete"}:
                self._stop_event.wait(interval)

    def _record_result(self, result: QueueWorkResult) -> None:
        key = {
            "idle": "idle_polls",
            "completed": "tasks_completed",
            "reconciling": "tasks_reconciling",
            "paused": "tasks_paused",
            "failed": "tasks_failed",
            "incomplete": "tasks_incomplete",
            "worker_error": "worker_errors",
        }[result.outcome]
        with self._lock:
            self._counts[key] += 1


__all__ = [
    "PersistentAgentWorkerSupervisor",
    "WorkerShutdownResult",
    "WorkerSupervisorMetrics",
    "WorkerSupervisorSettings",
    "load_worker_supervisor_settings",
]
