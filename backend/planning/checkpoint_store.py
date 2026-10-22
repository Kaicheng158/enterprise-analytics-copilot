"""PostgreSQL checkpoint repository for durable bounded planning.

Schema objects are created only by the versioned migration. The repository
performs transactional state, receipt, lease, and consistency operations.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, timedelta
from hashlib import sha256
import hmac
import json
import re
from typing import Callable
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from .checkpoint_models import (
    ExecutionConsistency,
    ExecutionCreation,
    PersistentCheckpoint,
    PersistentExecution,
    PersistentStepReceipt,
    ReconciliationCase,
    ReconciliationResolution,
    StepStart,
)
from .models import AgentState, Plan
from .transitions import StateTransitionError, validate_agent_transition
from .validation import canonical_plan_sha256


ConnectionFactory = Callable[[], AbstractContextManager[psycopg.Connection]]
HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
TERMINAL_STATUSES = frozenset({"completed", "failed"})


class CheckpointStoreError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ExecutionNotFound(CheckpointStoreError):
    pass


class IdempotencyConflict(CheckpointStoreError):
    pass


class LeaseUnavailable(CheckpointStoreError):
    pass


class CheckpointConflict(CheckpointStoreError):
    pass


class ReceiptConflict(CheckpointStoreError):
    pass


class AmbiguousExecution(CheckpointStoreError):
    pass


class QueueCapacityExceeded(CheckpointStoreError):
    pass


def canonical_json_sha256(value) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


class PostgresCheckpointStore:
    def __init__(
        self,
        connection_factory: ConnectionFactory,
        *,
        schema: str,
        idempotency_secret: bytes,
        max_active_executions: int | None = None,
    ):
        if not IDENTIFIER_RE.fullmatch(schema):
            raise ValueError("invalid checkpoint schema")
        if len(idempotency_secret) < 32:
            raise ValueError("idempotency secret must contain at least 32 bytes")
        if (
            max_active_executions is not None
            and not 1 <= max_active_executions <= 10_000
        ):
            raise ValueError("invalid active execution limit")
        self._connection_factory = connection_factory
        self._schema = schema
        self._secret = bytes(idempotency_secret)
        self._max_active_executions = max_active_executions

    def create_or_get_execution(
        self,
        *,
        scope_key: str,
        idempotency_key: str,
        request_sha256: str,
        plan: Plan,
        initial_state: AgentState,
        policy_version: str,
        runtime_version: str,
        now: datetime,
    ) -> ExecutionCreation:
        self._validate_time(now)
        self._validate_hash(request_sha256)
        if not 1 <= len(scope_key) <= 160:
            raise ValueError("invalid execution scope")
        if not 8 <= len(idempotency_key) <= 200:
            raise ValueError("invalid idempotency key")
        self._validate_initial_state(plan, initial_state)
        plan_hash = canonical_plan_sha256(plan)
        key_hash = self._idempotency_hash(scope_key, idempotency_key)
        state_json = initial_state.model_dump(mode="json")
        state_hash = canonical_json_sha256(state_json)
        execution_id = uuid4()
        plan_json = plan.model_dump(mode="json")
        insert = sql.SQL(
            """INSERT INTO {} (
                   execution_id,scope_key,idempotency_key_hash,request_sha256,
                   plan_sha256,plan_version,plan_json,policy_version,runtime_version,
                   status,state_version,checkpoint_sequence,created_at,updated_at
               ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',0,0,%s,%s)
               ON CONFLICT (scope_key,idempotency_key_hash) DO NOTHING
               RETURNING {}"""
        ).format(self._table("executions"), sql.SQL(self._execution_columns()))
        with self._connection_factory() as connection:
            with connection.transaction():
                if self._max_active_executions is not None:
                    connection.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 5701))",
                        (scope_key,),
                    )
                    existing_row = connection.execute(
                        sql.SQL(
                            "SELECT {} FROM {} WHERE scope_key=%s "
                            "AND idempotency_key_hash=%s FOR UPDATE"
                        ).format(
                            sql.SQL(self._execution_columns()),
                            self._table("executions"),
                        ),
                        (scope_key, key_hash),
                    ).fetchone()
                    if existing_row is not None:
                        execution = self._execution_from_row(existing_row)
                        if (
                            execution.request_sha256 != request_sha256
                            or execution.plan_sha256 != plan_hash
                            or execution.plan != plan
                        ):
                            raise IdempotencyConflict("idempotency_key_conflict")
                        checkpoint = self._latest_checkpoint(
                            connection, execution.execution_id
                        )
                        return ExecutionCreation(
                            execution=execution,
                            checkpoint=checkpoint,
                            created=False,
                        )
                    active_count = connection.execute(
                        sql.SQL(
                            "SELECT count(*) FROM {} WHERE scope_key=%s "
                            "AND status IN ('pending','running','reconciling')"
                        ).format(self._table("executions")),
                        (scope_key,),
                    ).fetchone()[0]
                    if active_count >= self._max_active_executions:
                        raise QueueCapacityExceeded("queue_capacity_exceeded")
                row = connection.execute(
                    insert,
                    (
                        execution_id,
                        scope_key,
                        key_hash,
                        request_sha256,
                        plan_hash,
                        plan.version,
                        Jsonb(plan_json),
                        policy_version,
                        runtime_version,
                        now,
                        now,
                    ),
                ).fetchone()
                created = row is not None
                if created:
                    connection.execute(
                        sql.SQL(
                            """INSERT INTO {} (
                                   execution_id,sequence,state_version,execution_status,
                                   agent_state,state_sha256,created_at
                               ) VALUES (%s,0,0,'pending',%s,%s,%s)"""
                        ).format(self._table("checkpoints")),
                        (execution_id, Jsonb(state_json), state_hash, now),
                    )
                    execution = self._execution_from_row(row)
                    checkpoint = PersistentCheckpoint(
                        execution_id=execution_id,
                        sequence=0,
                        state_version=0,
                        execution_status="pending",
                        agent_state=initial_state,
                        state_sha256=state_hash,
                        created_at=now,
                    )
                else:
                    row = connection.execute(
                        sql.SQL(
                            "SELECT {} FROM {} WHERE scope_key=%s AND idempotency_key_hash=%s FOR UPDATE"
                        ).format(
                            sql.SQL(self._execution_columns()),
                            self._table("executions"),
                        ),
                        (scope_key, key_hash),
                    ).fetchone()
                    execution = self._execution_from_row(row)
                    if (
                        execution.request_sha256 != request_sha256
                        or execution.plan_sha256 != plan_hash
                        or execution.plan != plan
                    ):
                        raise IdempotencyConflict("idempotency_key_conflict")
                    checkpoint = self._latest_checkpoint(connection, execution.execution_id)
        return ExecutionCreation(
            execution=execution,
            checkpoint=checkpoint,
            created=created,
        )

    def queue_status_counts(self, *, scope_key: str) -> dict[str, int]:
        if not 1 <= len(scope_key) <= 160:
            raise ValueError("invalid execution scope")
        counts = {"pending": 0, "running": 0, "reconciling": 0}
        with self._connection_factory() as connection:
            rows = connection.execute(
                sql.SQL(
                    "SELECT status,count(*) FROM {} WHERE scope_key=%s "
                    "AND status IN ('pending','running','reconciling') GROUP BY status"
                ).format(self._table("executions")),
                (scope_key,),
            ).fetchall()
        for status, count in rows:
            counts[status] = count
        counts["total"] = sum(counts.values())
        return counts

    def find_execution_by_idempotency(
        self,
        *,
        scope_key: str,
        idempotency_key: str,
        request_sha256: str,
    ) -> ExecutionCreation | None:
        """Find a prior submission before asking a planner to generate again.

        The raw idempotency key is HMACed exactly as it is during creation and
        never leaves this repository. A reused key with a different request
        fails closed before another provider call.
        """

        self._validate_hash(request_sha256)
        if not 1 <= len(scope_key) <= 160:
            raise ValueError("invalid execution scope")
        if not 8 <= len(idempotency_key) <= 200:
            raise ValueError("invalid idempotency key")
        key_hash = self._idempotency_hash(scope_key, idempotency_key)
        with self._connection_factory() as connection:
            row = connection.execute(
                sql.SQL(
                    "SELECT {} FROM {} WHERE scope_key=%s AND idempotency_key_hash=%s"
                ).format(
                    sql.SQL(self._execution_columns()),
                    self._table("executions"),
                ),
                (scope_key, key_hash),
            ).fetchone()
            if row is None:
                return None
            execution = self._execution_from_row(row)
            if execution.request_sha256 != request_sha256:
                raise IdempotencyConflict("idempotency_key_conflict")
            checkpoint = self._latest_checkpoint(connection, execution.execution_id)
        return ExecutionCreation(
            execution=execution,
            checkpoint=checkpoint,
            created=False,
        )

    def get_execution(self, execution_id: UUID) -> PersistentExecution:
        with self._connection_factory() as connection:
            row = connection.execute(
                sql.SQL("SELECT {} FROM {} WHERE execution_id=%s").format(
                    sql.SQL(self._execution_columns()), self._table("executions")
                ),
                (execution_id,),
            ).fetchone()
        if row is None:
            raise ExecutionNotFound("execution_not_found")
        return self._execution_from_row(row)

    def get_latest_checkpoint(self, execution_id: UUID) -> PersistentCheckpoint:
        with self._connection_factory() as connection:
            return self._latest_checkpoint(connection, execution_id)

    def list_receipts(self, execution_id: UUID) -> tuple[PersistentStepReceipt, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                sql.SQL(
                    "SELECT {} FROM {} WHERE execution_id=%s ORDER BY started_at,step_id"
                ).format(
                    sql.SQL(self._receipt_columns()), self._table("step_receipts")
                ),
                (execution_id,),
            ).fetchall()
        return tuple(self._receipt_from_row(row) for row in rows)

    def claim_next_execution(
        self,
        *,
        scope_key: str,
        worker_id: str,
        now: datetime,
        ttl_seconds: int,
    ) -> PersistentExecution | None:
        """Atomically lease the oldest runnable execution for one server scope.

        PostgreSQL row locking and SKIP LOCKED allow independent workers to poll
        concurrently without both receiving the same queue item. The existing
        Worker Runtime still revalidates this lease, state version, Checkpoint and
        Receipts before it starts or commits a step.
        """

        if not 1 <= len(scope_key) <= 160:
            raise ValueError("invalid execution scope")
        self._validate_worker(worker_id)
        self._validate_time(now)
        if not 1 <= ttl_seconds <= 300:
            raise ValueError("lease TTL must be between 1 and 300 seconds")
        expires = now + timedelta(seconds=ttl_seconds)
        query = sql.SQL(
            """WITH candidate AS (
                   SELECT execution_id FROM {}
                    WHERE scope_key=%s
                      AND status IN ('pending','running')
                      AND (lease_owner IS NULL OR lease_expires_at <= %s)
                    ORDER BY created_at, execution_id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
               )
               UPDATE {} AS execution
                  SET lease_owner=%s,lease_expires_at=%s,updated_at=%s
                 FROM candidate
                WHERE execution.execution_id=candidate.execution_id
               RETURNING {}"""
        ).format(
            self._table("executions"),
            self._table("executions"),
            sql.SQL(",").join(
                sql.Identifier("execution", item)
                for item in self._execution_columns().split(",")
            ),
        )
        with self._connection_factory() as connection:
            with connection.transaction():
                row = connection.execute(
                    query,
                    (scope_key, now, worker_id, expires, now),
                ).fetchone()
        return None if row is None else self._execution_from_row(row)

    def acquire_lease(
        self,
        execution_id: UUID,
        *,
        worker_id: str,
        now: datetime,
        ttl_seconds: int,
    ) -> PersistentExecution:
        self._validate_worker(worker_id)
        self._validate_time(now)
        if not 1 <= ttl_seconds <= 300:
            raise ValueError("lease TTL must be between 1 and 300 seconds")
        expires = now + timedelta(seconds=ttl_seconds)
        query = sql.SQL(
            """UPDATE {} SET lease_owner=%s,lease_expires_at=%s,updated_at=%s
               WHERE execution_id=%s
                 AND status IN ('pending','running')
                 AND (lease_owner IS NULL OR lease_expires_at <= %s OR lease_owner=%s)
               RETURNING {}"""
        ).format(self._table("executions"), sql.SQL(self._execution_columns()))
        with self._connection_factory() as connection:
            with connection.transaction():
                row = connection.execute(
                    query,
                    (worker_id, expires, now, execution_id, now, worker_id),
                ).fetchone()
        if row is None:
            if self._exists(execution_id):
                raise LeaseUnavailable("lease_unavailable")
            raise ExecutionNotFound("execution_not_found")
        return self._execution_from_row(row)

    def renew_lease(
        self,
        execution_id: UUID,
        *,
        worker_id: str,
        expected_state_version: int,
        now: datetime,
        ttl_seconds: int,
    ) -> PersistentExecution:
        """Heartbeat an active lease without changing durable state version."""

        self._validate_worker(worker_id)
        self._validate_time(now)
        if not 1 <= ttl_seconds <= 300:
            raise ValueError("lease TTL must be between 1 and 300 seconds")
        expires = now + timedelta(seconds=ttl_seconds)
        with self._connection_factory() as connection:
            with connection.transaction():
                row = connection.execute(
                    sql.SQL(
                        """UPDATE {} SET lease_expires_at=%s,updated_at=%s
                           WHERE execution_id=%s AND lease_owner=%s
                             AND lease_expires_at > %s
                             AND state_version=%s
                             AND status IN ('pending','running')
                           RETURNING {}"""
                    ).format(self._table("executions"), sql.SQL(self._execution_columns())),
                    (expires, now, execution_id, worker_id, now, expected_state_version),
                ).fetchone()
                if row is not None:
                    return self._execution_from_row(row)
                current_row = connection.execute(
                    sql.SQL("SELECT {} FROM {} WHERE execution_id=%s").format(
                        sql.SQL(self._execution_columns()), self._table("executions")
                    ),
                    (execution_id,),
                ).fetchone()
        if current_row is None:
            raise ExecutionNotFound("execution_not_found")
        current = self._execution_from_row(current_row)
        if current.state_version != expected_state_version:
            raise CheckpointConflict("checkpoint_cas_conflict")
        raise LeaseUnavailable("lease_unavailable")

    def release_lease(
        self,
        execution_id: UUID,
        *,
        worker_id: str,
        now: datetime,
    ) -> None:
        self._validate_worker(worker_id)
        self._validate_time(now)
        with self._connection_factory() as connection:
            with connection.transaction():
                cursor = connection.execute(
                    sql.SQL(
                        """UPDATE {} SET lease_owner=NULL,lease_expires_at=NULL,updated_at=%s
                           WHERE execution_id=%s AND lease_owner=%s"""
                    ).format(self._table("executions")),
                    (now, execution_id, worker_id),
                )
                if cursor.rowcount != 1:
                    raise LeaseUnavailable("lease_unavailable")

    def save_checkpoint(
        self,
        execution_id: UUID,
        *,
        worker_id: str,
        expected_state_version: int,
        execution_status: str,
        agent_state: AgentState,
        now: datetime,
    ) -> tuple[PersistentExecution, PersistentCheckpoint]:
        self._validate_worker(worker_id)
        self._validate_time(now)
        with self._connection_factory() as connection:
            with connection.transaction():
                execution = self._lock_execution(connection, execution_id)
                return self._write_checkpoint(
                    connection,
                    execution,
                    worker_id=worker_id,
                    expected_state_version=expected_state_version,
                    execution_status=execution_status,
                    agent_state=agent_state,
                    now=now,
                )

    def start_step(
        self,
        execution_id: UUID,
        *,
        worker_id: str,
        expected_state_version: int,
        running_state: AgentState,
        step_id: str,
        action_name: str,
        input_payload: dict,
        idempotency_token: str,
        now: datetime,
    ) -> StepStart:
        self._validate_worker(worker_id)
        self._validate_time(now)
        self._validate_hash(idempotency_token)
        input_hash = canonical_json_sha256(input_payload)
        with self._connection_factory() as connection:
            with connection.transaction():
                execution = self._lock_execution(connection, execution_id)
                self._ensure_lease(execution, worker_id, now)
                self._ensure_step_matches_plan(
                    execution.plan, step_id, action_name, input_payload
                )
                existing_row = connection.execute(
                    sql.SQL(
                        "SELECT {} FROM {} WHERE execution_id=%s AND step_id=%s FOR UPDATE"
                    ).format(
                        sql.SQL(self._receipt_columns()),
                        self._table("step_receipts"),
                    ),
                    (execution_id, step_id),
                ).fetchone()
                if existing_row is not None:
                    receipt = self._receipt_from_row(existing_row)
                    if (
                        receipt.idempotency_token != idempotency_token
                        or receipt.input_sha256 != input_hash
                        or receipt.action_name != action_name
                    ):
                        raise ReceiptConflict("step_receipt_conflict")
                    if receipt.status == "succeeded":
                        return StepStart(
                            execution=execution,
                            checkpoint=None,
                            receipt=receipt,
                            deduplicated=True,
                        )
                    raise AmbiguousExecution("step_outcome_requires_reconciliation")

                updated, checkpoint = self._write_checkpoint(
                    connection,
                    execution,
                    worker_id=worker_id,
                    expected_state_version=expected_state_version,
                    execution_status="running",
                    agent_state=running_state,
                    now=now,
                )
                connection.execute(
                    sql.SQL(
                        """INSERT INTO {} (
                               execution_id,step_id,action_name,idempotency_token,
                               input_sha256,status,started_at
                           ) VALUES (%s,%s,%s,%s,%s,'started',%s)"""
                    ).format(self._table("step_receipts")),
                    (
                        execution_id,
                        step_id,
                        action_name,
                        idempotency_token,
                        input_hash,
                        now,
                    ),
                )
                receipt = PersistentStepReceipt(
                    execution_id=execution_id,
                    step_id=step_id,
                    action_name=action_name,
                    idempotency_token=idempotency_token,
                    input_sha256=input_hash,
                    status="started",
                    output=None,
                    output_sha256=None,
                    tool_call_id=None,
                    error_code=None,
                    started_at=now,
                    finished_at=None,
                )
        return StepStart(
            execution=updated,
            checkpoint=checkpoint,
            receipt=receipt,
            deduplicated=False,
        )

    def complete_step(
        self,
        execution_id: UUID,
        *,
        worker_id: str,
        expected_state_version: int,
        completed_state: AgentState,
        step_id: str,
        idempotency_token: str,
        output: dict,
        tool_call_id: str | None,
        now: datetime,
    ) -> tuple[PersistentExecution, PersistentCheckpoint, PersistentStepReceipt]:
        self._validate_hash(idempotency_token)
        output_hash = canonical_json_sha256(output)
        with self._connection_factory() as connection:
            with connection.transaction():
                execution = self._lock_execution(connection, execution_id)
                receipt_row = connection.execute(
                    sql.SQL(
                        "SELECT {} FROM {} WHERE execution_id=%s AND step_id=%s FOR UPDATE"
                    ).format(
                        sql.SQL(self._receipt_columns()),
                        self._table("step_receipts"),
                    ),
                    (execution_id, step_id),
                ).fetchone()
                if receipt_row is None:
                    raise ReceiptConflict("step_receipt_missing")
                receipt = self._receipt_from_row(receipt_row)
                if (
                    receipt.status != "started"
                    or receipt.idempotency_token != idempotency_token
                ):
                    raise ReceiptConflict("step_receipt_conflict")
                step_state = next(
                    (state for state in completed_state.step_states if state.step_id == step_id),
                    None,
                )
                if step_state is None or step_state.status != "completed" or step_state.output != output:
                    raise CheckpointConflict("completed_state_output_mismatch")
                new_status = (
                    "completed" if completed_state.status == "completed" else "running"
                )
                updated, checkpoint = self._write_checkpoint(
                    connection,
                    execution,
                    worker_id=worker_id,
                    expected_state_version=expected_state_version,
                    execution_status=new_status,
                    agent_state=completed_state,
                    now=now,
                )
                row = connection.execute(
                    sql.SQL(
                        """UPDATE {} SET status='succeeded',output_json=%s,
                               output_sha256=%s,tool_call_id=%s,finished_at=%s
                           WHERE execution_id=%s AND step_id=%s AND status='started'
                           RETURNING {}"""
                    ).format(
                        self._table("step_receipts"),
                        sql.SQL(self._receipt_columns()),
                    ),
                    (
                        Jsonb(output),
                        output_hash,
                        tool_call_id,
                        now,
                        execution_id,
                        step_id,
                    ),
                ).fetchone()
                if row is None:
                    raise ReceiptConflict("step_receipt_conflict")
                completed_receipt = self._receipt_from_row(row)
        return updated, checkpoint, completed_receipt

    def mark_step_ambiguous(
        self,
        execution_id: UUID,
        *,
        worker_id: str,
        expected_state_version: int,
        step_id: str,
        now: datetime,
    ) -> tuple[PersistentExecution, PersistentCheckpoint, PersistentStepReceipt]:
        with self._connection_factory() as connection:
            with connection.transaction():
                execution = self._lock_execution(connection, execution_id)
                checkpoint = self._latest_checkpoint(connection, execution_id)
                row = connection.execute(
                    sql.SQL(
                        "SELECT {} FROM {} WHERE execution_id=%s AND step_id=%s FOR UPDATE"
                    ).format(
                        sql.SQL(self._receipt_columns()),
                        self._table("step_receipts"),
                    ),
                    (execution_id, step_id),
                ).fetchone()
                if row is None:
                    raise ReceiptConflict("step_receipt_missing")
                receipt = self._receipt_from_row(row)
                if receipt.status == "ambiguous":
                    raise AmbiguousExecution("step_outcome_requires_reconciliation")
                if receipt.status != "started":
                    raise ReceiptConflict("step_receipt_not_started")
                row = connection.execute(
                    sql.SQL(
                        """UPDATE {} SET status='ambiguous',error_code='outcome_unknown',
                               finished_at=%s
                           WHERE execution_id=%s AND step_id=%s AND status='started'
                           RETURNING {}"""
                    ).format(
                        self._table("step_receipts"),
                        sql.SQL(self._receipt_columns()),
                    ),
                    (now, execution_id, step_id),
                ).fetchone()
                updated, new_checkpoint = self._write_checkpoint(
                    connection,
                    execution,
                    worker_id=worker_id,
                    expected_state_version=expected_state_version,
                    execution_status="reconciling",
                    agent_state=checkpoint.agent_state,
                    now=now,
                )
                ambiguous = self._receipt_from_row(row)
        return updated, new_checkpoint, ambiguous

    def audit_execution_consistency(self, execution_id: UUID) -> ExecutionConsistency:
        """Fail closed if execution, latest checkpoint and receipts disagree."""

        execution = self.get_execution(execution_id)
        checkpoint = self.get_latest_checkpoint(execution_id)
        receipts = self.list_receipts(execution_id)
        if (
            execution.state_version != checkpoint.state_version
            or execution.checkpoint_sequence != checkpoint.sequence
            or execution.status != checkpoint.execution_status
            or execution.plan.plan_id != checkpoint.agent_state.plan_id
        ):
            raise CheckpointConflict("execution_checkpoint_inconsistent")
        plan_ids = {step.step_id for step in execution.plan.steps}
        states = {step.step_id: step for step in checkpoint.agent_state.step_states}
        if set(states) != plan_ids or any(receipt.step_id not in plan_ids for receipt in receipts):
            raise CheckpointConflict("execution_step_set_inconsistent")
        receipt_by_step = {receipt.step_id: receipt for receipt in receipts}
        for step_id, state in states.items():
            receipt = receipt_by_step.get(step_id)
            if state.status == "completed":
                if receipt is None or receipt.status != "succeeded" or receipt.output != state.output:
                    raise ReceiptConflict("checkpoint_receipt_inconsistent")
            elif receipt is not None and receipt.status == "succeeded":
                raise ReceiptConflict("checkpoint_receipt_inconsistent")
            if state.status == "running" and (receipt is None or receipt.status not in {"started", "ambiguous"}):
                raise ReceiptConflict("checkpoint_receipt_inconsistent")
        ambiguous = [receipt for receipt in receipts if receipt.status == "ambiguous"]
        if ambiguous and execution.status != "reconciling":
            raise ReceiptConflict("ambiguous_receipt_status_inconsistent")
        if execution.status == "reconciling" and len(ambiguous) != 1:
            raise ReceiptConflict("reconciliation_receipt_inconsistent")
        if execution.status == "completed" and (
            checkpoint.agent_state.status != "completed"
            or len(checkpoint.agent_state.completed_step_ids) != len(execution.plan.steps)
            or any(receipt.status != "succeeded" for receipt in receipts)
            or len(receipts) != len(execution.plan.steps)
        ):
            raise CheckpointConflict("completed_execution_inconsistent")
        return ExecutionConsistency(
            execution_id=execution_id,
            state_version=execution.state_version,
            checkpoint_sequence=execution.checkpoint_sequence,
            receipt_count=len(receipts),
            completed_receipt_count=sum(r.status == "succeeded" for r in receipts),
        )

    def get_reconciliation_case(self, execution_id: UUID) -> ReconciliationCase:
        """Expose a safe stop record; it never authorizes automatic retry."""

        self.audit_execution_consistency(execution_id)
        execution = self.get_execution(execution_id)
        if execution.status != "reconciling":
            raise AmbiguousExecution("reconciliation_not_required")
        ambiguous = [r for r in self.list_receipts(execution_id) if r.status == "ambiguous"]
        if len(ambiguous) != 1 or ambiguous[0].error_code != "outcome_unknown":
            raise ReceiptConflict("reconciliation_receipt_inconsistent")
        receipt = ambiguous[0]
        return ReconciliationCase(
            execution_id=execution_id,
            step_id=receipt.step_id,
            action_name=receipt.action_name,
            idempotency_token=receipt.idempotency_token,
        )

    def resolve_ambiguous_step(
        self,
        execution_id: UUID,
        *,
        expected_state_version: int,
        step_id: str,
        decision: str,
        resolved_state: AgentState,
        evidence_sha256: str,
        actor_id: str,
        now: datetime,
        output: dict | None = None,
        tool_call_id: str | None = None,
        error_code: str | None = None,
    ) -> ReconciliationResolution:
        """Atomically resolve one verified ambiguous outcome without retrying it."""

        self._validate_hash(evidence_sha256)
        self._validate_worker(actor_id)
        self._validate_time(now)
        if decision not in {"confirmed_succeeded", "confirmed_failed"}:
            raise ValueError("invalid reconciliation decision")
        if decision == "confirmed_succeeded":
            if output is None or error_code is not None:
                raise ValueError("successful reconciliation requires output")
            output_hash = canonical_json_sha256(output)
        else:
            if output is not None or tool_call_id is not None or not error_code or len(error_code) > 100:
                raise ValueError("failed reconciliation requires an error code")
            output_hash = None
        event_id = uuid4()
        with self._connection_factory() as connection:
            with connection.transaction():
                execution = self._lock_execution(connection, execution_id)
                if execution.status != "reconciling" or execution.lease_owner is not None:
                    raise AmbiguousExecution("reconciliation_not_required")
                if execution.state_version != expected_state_version:
                    raise CheckpointConflict("checkpoint_cas_conflict")
                checkpoint = self._latest_checkpoint(connection, execution_id)
                if checkpoint.state_version != expected_state_version:
                    raise CheckpointConflict("checkpoint_cas_conflict")
                receipt_row = connection.execute(
                    sql.SQL(
                        "SELECT {} FROM {} WHERE execution_id=%s AND step_id=%s FOR UPDATE"
                    ).format(sql.SQL(self._receipt_columns()), self._table("step_receipts")),
                    (execution_id, step_id),
                ).fetchone()
                if receipt_row is None:
                    raise ReceiptConflict("step_receipt_missing")
                receipt = self._receipt_from_row(receipt_row)
                if receipt.status != "ambiguous" or receipt.error_code != "outcome_unknown":
                    raise ReceiptConflict("step_receipt_not_ambiguous")
                step_state = next(
                    (state for state in resolved_state.step_states if state.step_id == step_id),
                    None,
                )
                if decision == "confirmed_succeeded":
                    if step_state is None or step_state.status != "completed" or step_state.output != output:
                        raise CheckpointConflict("reconciliation_state_output_mismatch")
                    new_status = "completed" if resolved_state.status == "completed" else "running"
                else:
                    if step_state is None or step_state.status != "failed" or resolved_state.status != "failed":
                        raise CheckpointConflict("reconciliation_failed_state_mismatch")
                    new_status = "failed"
                try:
                    validate_agent_transition(checkpoint.agent_state, resolved_state)
                except StateTransitionError as error:
                    raise CheckpointConflict("checkpoint_transition_invalid") from error
                self._validate_status_state(new_status, resolved_state)
                new_version = expected_state_version + 1
                state_json = resolved_state.model_dump(mode="json")
                state_hash = canonical_json_sha256(state_json)
                updated_row = connection.execute(
                    sql.SQL(
                        """UPDATE {} SET status=%s,state_version=%s,checkpoint_sequence=%s,
                               lease_owner=NULL,lease_expires_at=NULL,updated_at=%s
                           WHERE execution_id=%s AND state_version=%s
                             AND status='reconciling' AND lease_owner IS NULL
                           RETURNING {}"""
                    ).format(self._table("executions"), sql.SQL(self._execution_columns())),
                    (new_status, new_version, new_version, now, execution_id, expected_state_version),
                ).fetchone()
                if updated_row is None:
                    raise CheckpointConflict("checkpoint_cas_conflict")
                connection.execute(
                    sql.SQL(
                        """INSERT INTO {} (execution_id,sequence,state_version,execution_status,
                               agent_state,state_sha256,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)"""
                    ).format(self._table("checkpoints")),
                    (execution_id, new_version, new_version, new_status, Jsonb(state_json), state_hash, now),
                )
                if decision == "confirmed_succeeded":
                    resolved_row = connection.execute(
                        sql.SQL(
                            """UPDATE {} SET status='succeeded',output_json=%s,output_sha256=%s,
                                   tool_call_id=%s,error_code=NULL,finished_at=%s
                               WHERE execution_id=%s AND step_id=%s AND status='ambiguous'
                               RETURNING {}"""
                        ).format(self._table("step_receipts"), sql.SQL(self._receipt_columns())),
                        (Jsonb(output), output_hash, tool_call_id, now, execution_id, step_id),
                    ).fetchone()
                else:
                    resolved_row = connection.execute(
                        sql.SQL(
                            """UPDATE {} SET status='failed',error_code=%s,finished_at=%s
                               WHERE execution_id=%s AND step_id=%s AND status='ambiguous'
                               RETURNING {}"""
                        ).format(self._table("step_receipts"), sql.SQL(self._receipt_columns())),
                        (error_code, now, execution_id, step_id),
                    ).fetchone()
                if resolved_row is None:
                    raise ReceiptConflict("step_receipt_conflict")
                connection.execute(
                    sql.SQL(
                        """INSERT INTO {} (event_id,execution_id,step_id,decision,evidence_sha256,actor_id,created_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)"""
                    ).format(self._table("reconciliation_events")),
                    (event_id, execution_id, step_id, decision, evidence_sha256, actor_id, now),
                )
                updated = self._execution_from_row(updated_row)
                new_checkpoint = PersistentCheckpoint(
                    execution_id=execution_id, sequence=new_version, state_version=new_version,
                    execution_status=new_status, agent_state=resolved_state,
                    state_sha256=state_hash, created_at=now,
                )
                resolved_receipt = self._receipt_from_row(resolved_row)
        return ReconciliationResolution(
            event_id=event_id, execution=updated, checkpoint=new_checkpoint,
            receipt=resolved_receipt, decision=decision, evidence_sha256=evidence_sha256,
            actor_id=actor_id, created_at=now,
        )

    def derive_step_token(
        self,
        execution_id: UUID,
        plan_sha256: str,
        step_id: str,
    ) -> str:
        self._validate_hash(plan_sha256)
        payload = f"{execution_id}\0{plan_sha256}\0{step_id}".encode("utf-8")
        return hmac.new(self._secret, payload, sha256).hexdigest()

    def _write_checkpoint(
        self,
        connection,
        execution: PersistentExecution,
        *,
        worker_id: str,
        expected_state_version: int,
        execution_status: str,
        agent_state: AgentState,
        now: datetime,
    ) -> tuple[PersistentExecution, PersistentCheckpoint]:
        self._ensure_lease(execution, worker_id, now)
        if execution.state_version != expected_state_version:
            raise CheckpointConflict("checkpoint_cas_conflict")
        latest = self._latest_checkpoint(connection, execution.execution_id)
        if latest.state_version != expected_state_version:
            raise CheckpointConflict("checkpoint_cas_conflict")
        if latest.agent_state == agent_state:
            if execution_status != "reconciling" or execution.status == "reconciling":
                raise CheckpointConflict("checkpoint_noop")
        else:
            try:
                validate_agent_transition(latest.agent_state, agent_state)
            except StateTransitionError as error:
                raise CheckpointConflict("checkpoint_transition_invalid") from error
        self._validate_status_state(execution_status, agent_state)
        new_version = execution.state_version + 1
        new_sequence = execution.checkpoint_sequence + 1
        clear_lease = execution_status in TERMINAL_STATUSES or execution_status == "reconciling"
        state_json = agent_state.model_dump(mode="json")
        state_hash = canonical_json_sha256(state_json)
        row = connection.execute(
            sql.SQL(
                """UPDATE {} SET status=%s,state_version=%s,checkpoint_sequence=%s,
                       lease_owner=CASE WHEN %s THEN NULL ELSE lease_owner END,
                       lease_expires_at=CASE WHEN %s THEN NULL ELSE lease_expires_at END,
                       updated_at=%s
                   WHERE execution_id=%s AND state_version=%s AND lease_owner=%s
                   RETURNING {}"""
            ).format(self._table("executions"), sql.SQL(self._execution_columns())),
            (
                execution_status,
                new_version,
                new_sequence,
                clear_lease,
                clear_lease,
                now,
                execution.execution_id,
                expected_state_version,
                worker_id,
            ),
        ).fetchone()
        if row is None:
            raise CheckpointConflict("checkpoint_cas_conflict")
        connection.execute(
            sql.SQL(
                """INSERT INTO {} (
                       execution_id,sequence,state_version,execution_status,
                       agent_state,state_sha256,created_at
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s)"""
            ).format(self._table("checkpoints")),
            (
                execution.execution_id,
                new_sequence,
                new_version,
                execution_status,
                Jsonb(state_json),
                state_hash,
                now,
            ),
        )
        updated = self._execution_from_row(row)
        checkpoint = PersistentCheckpoint(
            execution_id=execution.execution_id,
            sequence=new_sequence,
            state_version=new_version,
            execution_status=execution_status,
            agent_state=agent_state,
            state_sha256=state_hash,
            created_at=now,
        )
        return updated, checkpoint

    def _lock_execution(self, connection, execution_id: UUID) -> PersistentExecution:
        row = connection.execute(
            sql.SQL("SELECT {} FROM {} WHERE execution_id=%s FOR UPDATE").format(
                sql.SQL(self._execution_columns()), self._table("executions")
            ),
            (execution_id,),
        ).fetchone()
        if row is None:
            raise ExecutionNotFound("execution_not_found")
        return self._execution_from_row(row)

    def _latest_checkpoint(self, connection, execution_id: UUID) -> PersistentCheckpoint:
        row = connection.execute(
            sql.SQL(
                """SELECT execution_id,sequence,state_version,execution_status,
                          agent_state,state_sha256,created_at
                   FROM {} WHERE execution_id=%s ORDER BY sequence DESC LIMIT 1"""
            ).format(self._table("checkpoints")),
            (execution_id,),
        ).fetchone()
        if row is None:
            raise ExecutionNotFound("checkpoint_not_found")
        state = AgentState.model_validate_json(
            json.dumps(row[4], ensure_ascii=False), strict=True
        )
        state_json = state.model_dump(mode="json")
        if canonical_json_sha256(state_json) != row[5]:
            raise CheckpointConflict("checkpoint_integrity_invalid")
        self._validate_status_state(row[3], state)
        return PersistentCheckpoint(
            execution_id=row[0],
            sequence=row[1],
            state_version=row[2],
            execution_status=row[3],
            agent_state=state,
            state_sha256=row[5],
            created_at=row[6],
        )

    def _execution_from_row(self, row) -> PersistentExecution:
        if row is None:
            raise ExecutionNotFound("execution_not_found")
        plan = Plan.model_validate_json(
            json.dumps(row[6], ensure_ascii=False), strict=True
        )
        if row[5] != plan.version or row[4] != canonical_plan_sha256(plan):
            raise CheckpointConflict("execution_plan_integrity_invalid")
        return PersistentExecution(
            execution_id=row[0],
            scope_key=row[1],
            idempotency_key_hash=row[2],
            request_sha256=row[3],
            plan_sha256=row[4],
            plan_version=row[5],
            plan=plan,
            policy_version=row[7],
            runtime_version=row[8],
            status=row[9],
            state_version=row[10],
            checkpoint_sequence=row[11],
            lease_owner=row[12],
            lease_expires_at=row[13],
            created_at=row[14],
            updated_at=row[15],
        )

    @staticmethod
    def _receipt_from_row(row) -> PersistentStepReceipt:
        if row[5] == "succeeded" and canonical_json_sha256(row[6]) != row[7]:
            raise ReceiptConflict("step_receipt_integrity_invalid")
        return PersistentStepReceipt(
            execution_id=row[0],
            step_id=row[1],
            action_name=row[2],
            idempotency_token=row[3],
            input_sha256=row[4],
            status=row[5],
            output=row[6],
            output_sha256=row[7],
            tool_call_id=row[8],
            error_code=row[9],
            started_at=row[10],
            finished_at=row[11],
        )

    def _ensure_step_matches_plan(
        self,
        plan: Plan,
        step_id: str,
        action_name: str,
        input_payload: dict,
    ) -> None:
        step = next((item for item in plan.steps if item.step_id == step_id), None)
        if step is None:
            raise ReceiptConflict("step_not_in_plan")
        expected_action = (
            step.tool_call.name if step.kind == "tool" else step.computation.operation
        )
        expected_input = (
            step.tool_call.arguments
            if step.kind == "tool"
            else step.computation.model_dump(mode="json")
        )
        if action_name != expected_action or input_payload != expected_input:
            raise ReceiptConflict("step_input_mismatch")

    @staticmethod
    def _validate_initial_state(plan: Plan, state: AgentState) -> None:
        expected_steps = tuple((step.step_id, step.kind) for step in plan.steps)
        actual_steps = tuple((step.step_id, step.kind) for step in state.step_states)
        if (
            state.plan_id != plan.plan_id
            or state.status != "pending"
            or state.current_step_id is not None
            or actual_steps != expected_steps
            or any(
                step.status != "pending"
                or step.attempts != 0
                or step.output is not None
                or step.error is not None
                or step.tool_call_id is not None
                for step in state.step_states
            )
            or state.completed_step_ids
            or state.steps_executed != 0
            or state.tool_calls_used != 0
            or state.budget_units_used != 0
            or state.elapsed_ms != 0
            or state.error is not None
        ):
            raise ValueError("initial checkpoint does not match plan")

    @staticmethod
    def _validate_status_state(status: str, state: AgentState) -> None:
        if status not in {"pending", "running", "reconciling", "completed", "failed"}:
            raise ValueError("invalid durable execution status")
        if status == "completed" and state.status != "completed":
            raise CheckpointConflict("checkpoint_status_mismatch")
        if status == "failed" and state.status != "failed":
            raise CheckpointConflict("checkpoint_status_mismatch")
        if status == "running" and state.status not in {"pending", "running"}:
            raise CheckpointConflict("checkpoint_status_mismatch")

    @staticmethod
    def _ensure_lease(
        execution: PersistentExecution,
        worker_id: str,
        now: datetime,
    ) -> None:
        if (
            execution.lease_owner != worker_id
            or execution.lease_expires_at is None
            or execution.lease_expires_at <= now
        ):
            raise LeaseUnavailable("lease_unavailable")

    def _exists(self, execution_id: UUID) -> bool:
        with self._connection_factory() as connection:
            return (
                connection.execute(
                    sql.SQL("SELECT 1 FROM {} WHERE execution_id=%s").format(
                        self._table("executions")
                    ),
                    (execution_id,),
                ).fetchone()
                is not None
            )

    def _idempotency_hash(self, scope_key: str, key: str) -> str:
        payload = f"{scope_key}\0{key}".encode("utf-8")
        return hmac.new(self._secret, payload, sha256).hexdigest()

    @staticmethod
    def _validate_hash(value: str) -> None:
        if not HEX_SHA256_RE.fullmatch(value):
            raise ValueError("invalid SHA-256 value")

    @staticmethod
    def _validate_worker(worker_id: str) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,127}", worker_id):
            raise ValueError("invalid worker ID")

    @staticmethod
    def _validate_time(value: datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")

    def _table(self, name: str):
        return sql.Identifier(self._schema, name)

    @staticmethod
    def _execution_columns() -> str:
        return (
            "execution_id,scope_key,idempotency_key_hash,request_sha256,"
            "plan_sha256,plan_version,plan_json,policy_version,runtime_version,"
            "status,state_version,checkpoint_sequence,lease_owner,lease_expires_at,"
            "created_at,updated_at"
        )

    @staticmethod
    def _receipt_columns() -> str:
        return (
            "execution_id,step_id,action_name,idempotency_token,input_sha256,status,"
            "output_json,output_sha256,tool_call_id,error_code,started_at,finished_at"
        )
