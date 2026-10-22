"""Default-off PostgreSQL-backed queue worker for durable Agent executions."""

from __future__ import annotations

from datetime import UTC, datetime
import logging
import os
import re
from threading import Event
from typing import Callable, Literal
from uuid import UUID

from pydantic import Field, model_validator

from backend.tools.models import StrictModel

from .checkpoint_store import PostgresCheckpointStore
from .durable_agent_runtime import DurableAgentRuntime


LOGGER = logging.getLogger("uvicorn.error")
QUEUE_WORKER_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,80}$")


class PersistentAgentQueueSettings(StrictModel):
    enabled: bool = False
    lease_ttl_seconds: int = Field(default=2, ge=1, le=300)
    poll_interval_ms: int = Field(default=100, ge=25, le=60_000)
    max_tasks_per_drain: int = Field(default=100, ge=1, le=1_000)


def load_persistent_agent_queue_settings() -> PersistentAgentQueueSettings:
    raw = os.getenv("PERSISTENT_AGENT_QUEUE_ENABLED", "false").strip().lower()
    values = {"true": True, "false": False, "1": True, "0": False}
    if raw not in values:
        raise ValueError("Invalid PERSISTENT_AGENT_QUEUE_ENABLED value")
    return PersistentAgentQueueSettings(enabled=values[raw])


class QueueWorkResult(StrictModel):
    outcome: Literal[
        "idle",
        "completed",
        "reconciling",
        "paused",
        "failed",
        "incomplete",
        "worker_error",
    ]
    execution_id: UUID | None = None
    durable_steps: int = Field(default=0, ge=0, le=8)

    @model_validator(mode="after")
    def execution_matches_outcome(self):
        if (self.outcome == "idle") != (self.execution_id is None):
            raise ValueError("only an idle queue result omits execution_id")
        return self


class PersistentAgentQueueWorker:
    """Poll and execute persisted tasks without depending on the HTTP process."""

    def __init__(
        self,
        store: PostgresCheckpointStore,
        agent: DurableAgentRuntime,
        *,
        scope_key: str,
        settings: PersistentAgentQueueSettings | None = None,
    ):
        self.settings = settings or load_persistent_agent_queue_settings()
        if not self.settings.enabled:
            raise ValueError("persistent_agent_queue_disabled")
        if not 1 <= len(scope_key) <= 160:
            raise ValueError("invalid queue scope")
        self._store = store
        self._agent = agent
        self._scope_key = scope_key

    @staticmethod
    def worker_prefix(worker_id: str) -> str:
        if not QUEUE_WORKER_ID_RE.fullmatch(worker_id):
            raise ValueError("invalid queue worker ID")
        return f"queue-{worker_id}"

    def run_once(
        self,
        *,
        worker_id: str,
        on_claimed: Callable[[UUID], None] | None = None,
    ) -> QueueWorkResult:
        if not QUEUE_WORKER_ID_RE.fullmatch(worker_id):
            raise ValueError("invalid queue worker ID")
        # The first DurableAgentRuntime step uses suffix '-0'. Claim with that
        # exact owner so the existing recovery path can safely renew the lease.
        worker_prefix = self.worker_prefix(worker_id)
        claim_owner = f"{worker_prefix}-0"
        claimed = self._store.claim_next_execution(
            scope_key=self._scope_key,
            worker_id=claim_owner,
            now=datetime.now(UTC),
            ttl_seconds=self.settings.lease_ttl_seconds,
        )
        if claimed is None:
            return QueueWorkResult(outcome="idle")
        if on_claimed is not None:
            try:
                on_claimed(claimed.execution_id)
            except Exception:
                # Metrics/lifecycle observers must never authorize, block, or
                # change a durable task execution.
                LOGGER.info(
                    "persistent_agent_queue %s",
                    {"status": "claim_observer_error"},
                )

        try:
            result = self._agent.run_to_terminal(
                claimed.execution_id,
                worker_id_prefix=worker_prefix,
            )
        except Exception:
            LOGGER.info(
                "persistent_agent_queue %s",
                {
                    "status": "worker_error",
                    "execution_id": str(claimed.execution_id),
                },
            )
            return QueueWorkResult(
                outcome="worker_error",
                execution_id=claimed.execution_id,
            )
        mapped = {
            "completed": "completed",
            "reconciling": "reconciling",
            "paused": "paused",
            "failed": "failed",
            "incomplete": "incomplete",
        }[result.status]
        LOGGER.info(
            "persistent_agent_queue %s",
            {
                "status": mapped,
                "execution_id": str(claimed.execution_id),
                "durable_steps": len(result.worker_outcomes),
            },
        )
        return QueueWorkResult(
            outcome=mapped,
            execution_id=claimed.execution_id,
            durable_steps=len(result.worker_outcomes),
        )

    def drain(self, *, worker_id: str) -> tuple[QueueWorkResult, ...]:
        results = []
        for _ in range(self.settings.max_tasks_per_drain):
            result = self.run_once(worker_id=worker_id)
            results.append(result)
            if result.outcome == "idle":
                break
        return tuple(results)

    def run_forever(self, *, worker_id: str, stop_event: Event) -> None:
        if not isinstance(stop_event, Event):
            raise TypeError("stop_event must be threading.Event")
        interval = self.settings.poll_interval_ms / 1_000
        while not stop_event.is_set():
            try:
                result = self.run_once(worker_id=worker_id)
            except Exception:
                LOGGER.info(
                    "persistent_agent_queue %s",
                    {"status": "poll_error"},
                )
                stop_event.wait(interval)
                continue
            if result.outcome in {"idle", "worker_error", "paused", "incomplete"}:
                stop_event.wait(interval)


__all__ = [
    "PersistentAgentQueueSettings",
    "PersistentAgentQueueWorker",
    "QueueWorkResult",
    "load_persistent_agent_queue_settings",
]
