import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import uuid4

from backend.planning.checkpoint_models import LeaseSession
from backend.planning.checkpoint_store import CheckpointConflict
from backend.planning.reconciliation import (
    ReconciliationAuthorizationError,
    ReconciliationPolicy,
    ReconciliationService,
)
from backend.planning.worker_coordination import WorkerLeaseCoordinator


class WorkerCoordinationContractTests(unittest.TestCase):
    def test_lease_ttl_is_bounded(self):
        with self.assertRaisesRegex(ValueError, "lease TTL"):
            WorkerLeaseCoordinator(Mock(), ttl_seconds=0)
        with self.assertRaisesRegex(ValueError, "lease TTL"):
            WorkerLeaseCoordinator(Mock(), ttl_seconds=301)

    def test_heartbeat_uses_server_session_identity_and_version(self):
        execution_id = uuid4()
        now = datetime(2026, 10, 21, tzinfo=UTC)
        execution = Mock(
            execution_id=execution_id,
            lease_owner="worker-one",
            lease_expires_at=now + timedelta(seconds=10),
            state_version=4,
        )
        store = Mock()
        store.renew_lease.return_value = execution
        coordinator = WorkerLeaseCoordinator(store, ttl_seconds=10)
        session = LeaseSession(
            execution_id=execution_id,
            worker_id="worker-one",
            state_version=4,
            lease_expires_at=now + timedelta(seconds=5),
        )
        updated = coordinator.heartbeat(session, now=now)
        self.assertEqual(updated.state_version, 4)
        store.renew_lease.assert_called_once_with(
            execution_id,
            worker_id="worker-one",
            expected_state_version=4,
            now=now,
            ttl_seconds=10,
        )

    def test_assert_current_rejects_advanced_state(self):
        execution_id = uuid4()
        now = datetime(2026, 10, 21, tzinfo=UTC)
        store = Mock()
        store.get_execution.return_value = Mock(state_version=2)
        session = LeaseSession(
            execution_id=execution_id,
            worker_id="worker-one",
            state_version=1,
            lease_expires_at=now + timedelta(seconds=5),
        )
        with self.assertRaisesRegex(CheckpointConflict, "checkpoint_cas_conflict"):
            WorkerLeaseCoordinator(store).assert_current(session, now=now)

    def test_unauthorized_reconciliation_stops_before_store_access(self):
        store = Mock()
        service = ReconciliationService(
            store,
            ReconciliationPolicy(allowed_actors=frozenset({"trusted-worker"})),
            Mock(),
        )
        with self.assertRaisesRegex(
            ReconciliationAuthorizationError, "reconciliation_actor_not_authorized"
        ):
            service.confirm_failed(
                uuid4(),
                step_id="load_baseline_sales",
                expected_state_version=2,
                evidence_sha256="a" * 64,
                actor_id="ordinary-worker",
                now=datetime(2026, 10, 21, tzinfo=UTC),
            )
        store.get_execution.assert_not_called()


if __name__ == "__main__":
    unittest.main()
