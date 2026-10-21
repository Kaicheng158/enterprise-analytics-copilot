CREATE TABLE agent_runtime.reconciliation_events (
    event_id uuid PRIMARY KEY,
    execution_id uuid NOT NULL,
    step_id text NOT NULL,
    decision text NOT NULL CHECK (decision IN ('confirmed_succeeded','confirmed_failed')),
    evidence_sha256 text NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
    actor_id text NOT NULL CHECK (actor_id ~ '^[a-z][a-z0-9_-]{0,127}$'),
    created_at timestamptz NOT NULL,
    UNIQUE (execution_id, step_id),
    FOREIGN KEY (execution_id, step_id)
        REFERENCES agent_runtime.step_receipts(execution_id, step_id)
        ON DELETE CASCADE
);

CREATE INDEX agent_runtime_reconciliation_created
    ON agent_runtime.reconciliation_events(created_at, execution_id);

REVOKE ALL ON agent_runtime.reconciliation_events FROM PUBLIC;
