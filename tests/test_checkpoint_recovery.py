import unittest
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import ValidationError

from backend.planning.checkpoint_models import (
    PersistentCheckpoint,
    PersistentExecution,
    PersistentStepReceipt,
    RecoveryDecision,
)
from backend.planning.checkpoint_recovery import initial_agent_state
from backend.planning.checkpoint_store import (
    PostgresCheckpointStore,
    canonical_json_sha256,
)
from backend.planning.demo import build_quarter_sales_comparison_plan
from backend.planning.models import PlanningRequest
from backend.planning.validation import canonical_plan_sha256


NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)
HASH = "a" * 64


class CheckpointRecoveryContractTests(unittest.TestCase):
    def setUp(self):
        self.plan = build_quarter_sales_comparison_plan(PlanningRequest())
        self.state = initial_agent_state(self.plan)

    def execution_payload(self):
        return {
            "execution_id": uuid4(),
            "scope_key": "tenant-a:user-a:quarterly-sales",
            "idempotency_key_hash": HASH,
            "request_sha256": HASH,
            "plan_sha256": canonical_plan_sha256(self.plan),
            "plan_version": self.plan.version,
            "plan": self.plan,
            "policy_version": "bounded-planning-v1",
            "runtime_version": "checkpoint-simulator-v1",
            "status": "pending",
            "state_version": 0,
            "checkpoint_sequence": 0,
            "lease_owner": None,
            "lease_expires_at": None,
            "created_at": NOW,
            "updated_at": NOW,
        }

    def test_initial_state_is_bound_to_every_plan_step(self):
        self.assertEqual(self.state.plan_id, self.plan.plan_id)
        self.assertEqual(self.state.status, "pending")
        self.assertEqual(
            tuple(item.step_id for item in self.state.step_states),
            tuple(item.step_id for item in self.plan.steps),
        )
        self.assertTrue(all(item.status == "pending" for item in self.state.step_states))
        self.assertEqual(self.state.completed_step_ids, ())

    def test_canonical_json_hash_is_stable_across_key_order(self):
        self.assertEqual(
            canonical_json_sha256({"b": [2, 1], "a": "值"}),
            canonical_json_sha256({"a": "值", "b": [2, 1]}),
        )
        self.assertNotEqual(
            canonical_json_sha256({"a": 1}),
            canonical_json_sha256({"a": "1"}),
        )

    def test_store_rejects_unsafe_schema_and_short_hmac_secret(self):
        factory = lambda: None
        with self.assertRaisesRegex(ValueError, "invalid checkpoint schema"):
            PostgresCheckpointStore(
                factory,
                schema="public; DROP TABLE executions",
                idempotency_secret=b"x" * 32,
            )
        with self.assertRaisesRegex(ValueError, "at least 32 bytes"):
            PostgresCheckpointStore(
                factory,
                schema="planning_checkpoint_test",
                idempotency_secret=b"too-short",
            )

    def test_lease_owner_and_expiry_must_be_paired(self):
        payload = self.execution_payload()
        payload["lease_owner"] = "worker-a"
        with self.assertRaises(ValidationError):
            PersistentExecution.model_validate(payload, strict=True)

    def test_execution_versions_and_timestamps_are_monotonic(self):
        payload = self.execution_payload()
        payload["state_version"] = 1
        with self.assertRaises(ValidationError):
            PersistentExecution.model_validate(payload, strict=True)

        payload = self.execution_payload()
        payload["updated_at"] = datetime(2026, 10, 19, tzinfo=timezone.utc)
        with self.assertRaises(ValidationError):
            PersistentExecution.model_validate(payload, strict=True)

    def test_checkpoint_requires_timezone_aware_time(self):
        with self.assertRaises(ValidationError):
            PersistentCheckpoint(
                execution_id=uuid4(),
                sequence=0,
                state_version=0,
                execution_status="pending",
                agent_state=self.state,
                state_sha256=HASH,
                created_at=datetime(2026, 10, 20),
            )

    def test_checkpoint_sequence_must_match_state_version(self):
        with self.assertRaises(ValidationError):
            PersistentCheckpoint(
                execution_id=uuid4(),
                sequence=1,
                state_version=0,
                execution_status="pending",
                agent_state=self.state,
                state_sha256=HASH,
                created_at=NOW,
            )

    def test_receipt_status_contract_rejects_partial_success(self):
        with self.assertRaises(ValidationError):
            PersistentStepReceipt(
                execution_id=uuid4(),
                step_id="load_baseline_sales",
                action_name="get_postgres_sales_summary",
                idempotency_token=HASH,
                input_sha256=HASH,
                status="succeeded",
                output={"total_sales": "400.00"},
                output_sha256=None,
                tool_call_id="call-1",
                error_code=None,
                started_at=NOW,
                finished_at=NOW,
            )

    def test_receipt_finish_cannot_precede_start(self):
        with self.assertRaises(ValidationError):
            PersistentStepReceipt(
                execution_id=uuid4(),
                step_id="load_baseline_sales",
                action_name="get_postgres_sales_summary",
                idempotency_token=HASH,
                input_sha256=HASH,
                status="ambiguous",
                output=None,
                output_sha256=None,
                tool_call_id=None,
                error_code="outcome_unknown",
                started_at=NOW,
                finished_at=datetime(2026, 10, 19, tzinfo=timezone.utc),
            )

    def test_recovery_decision_exposes_next_step_only_when_resumable(self):
        with self.assertRaises(ValidationError):
            RecoveryDecision(
                outcome="reconciliation_required",
                execution_id=uuid4(),
                state_version=1,
                next_step_id="load_baseline_sales",
                reason="ambiguous_step",
            )

    def test_step_tokens_bind_execution_plan_and_step_without_raw_key(self):
        store = PostgresCheckpointStore(
            lambda: None,
            schema="planning_checkpoint_test",
            idempotency_secret=b"synthetic-test-secret-material-00001",
        )
        execution_id = uuid4()
        plan_hash = canonical_plan_sha256(self.plan)
        baseline = store.derive_step_token(
            execution_id, plan_hash, "load_baseline_sales"
        )
        self.assertEqual(
            baseline,
            store.derive_step_token(
                execution_id, plan_hash, "load_baseline_sales"
            ),
        )
        self.assertNotEqual(
            baseline,
            store.derive_step_token(
                execution_id, plan_hash, "load_comparison_sales"
            ),
        )


if __name__ == "__main__":
    unittest.main()
