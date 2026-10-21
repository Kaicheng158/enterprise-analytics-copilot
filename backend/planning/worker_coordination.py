"""Server-owned worker lease coordination for durable plan execution."""

from datetime import datetime
from uuid import UUID

from .checkpoint_models import LeaseSession
from .checkpoint_store import CheckpointConflict, LeaseUnavailable, PostgresCheckpointStore


class WorkerLeaseCoordinator:
    def __init__(self, store: PostgresCheckpointStore, *, ttl_seconds: int = 10):
        if not 1 <= ttl_seconds <= 300:
            raise ValueError("lease TTL must be between 1 and 300 seconds")
        self._store = store
        self._ttl_seconds = ttl_seconds

    def claim(self, execution_id: UUID, *, worker_id: str, now: datetime) -> LeaseSession:
        execution = self._store.acquire_lease(
            execution_id, worker_id=worker_id, now=now, ttl_seconds=self._ttl_seconds
        )
        return self._session(execution)

    def heartbeat(self, session: LeaseSession, *, now: datetime) -> LeaseSession:
        execution = self._store.renew_lease(
            session.execution_id,
            worker_id=session.worker_id,
            expected_state_version=session.state_version,
            now=now,
            ttl_seconds=self._ttl_seconds,
        )
        return self._session(execution)

    def assert_current(self, session: LeaseSession, *, now: datetime) -> None:
        if now.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        execution = self._store.get_execution(session.execution_id)
        if execution.state_version != session.state_version:
            raise CheckpointConflict("checkpoint_cas_conflict")
        if (
            execution.status not in {"pending", "running"}
            or execution.lease_owner != session.worker_id
            or execution.lease_expires_at is None
            or execution.lease_expires_at <= now
        ):
            raise LeaseUnavailable("lease_unavailable")

    @staticmethod
    def _session(execution) -> LeaseSession:
        if execution.lease_owner is None or execution.lease_expires_at is None:
            raise LeaseUnavailable("lease_unavailable")
        return LeaseSession(
            execution_id=execution.execution_id,
            worker_id=execution.lease_owner,
            state_version=execution.state_version,
            lease_expires_at=execution.lease_expires_at,
        )
