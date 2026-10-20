CREATE SCHEMA planning_checkpoint_test;
REVOKE ALL ON SCHEMA planning_checkpoint_test FROM PUBLIC;

CREATE TABLE planning_checkpoint_test.executions (
    execution_id uuid PRIMARY KEY,
    scope_key text NOT NULL CHECK (length(scope_key) BETWEEN 1 AND 160),
    idempotency_key_hash text NOT NULL CHECK (idempotency_key_hash ~ '^[0-9a-f]{64}$'),
    request_sha256 text NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    plan_sha256 text NOT NULL CHECK (plan_sha256 ~ '^[0-9a-f]{64}$'),
    plan_version text NOT NULL,
    plan_json jsonb NOT NULL,
    policy_version text NOT NULL,
    runtime_version text NOT NULL,
    status text NOT NULL CHECK (status IN ('pending','running','reconciling','completed','failed')),
    state_version bigint NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    checkpoint_sequence bigint NOT NULL DEFAULT 0 CHECK (checkpoint_sequence >= 0),
    lease_owner text,
    lease_expires_at timestamptz,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    UNIQUE (scope_key, idempotency_key_hash),
    CHECK ((lease_owner IS NULL) = (lease_expires_at IS NULL)),
    CHECK (state_version = checkpoint_sequence),
    CHECK (updated_at >= created_at)
);

CREATE TABLE planning_checkpoint_test.checkpoints (
    execution_id uuid NOT NULL REFERENCES planning_checkpoint_test.executions(execution_id) ON DELETE CASCADE,
    sequence bigint NOT NULL CHECK (sequence >= 0),
    state_version bigint NOT NULL CHECK (state_version >= 0),
    execution_status text NOT NULL CHECK (execution_status IN ('pending','running','reconciling','completed','failed')),
    agent_state jsonb NOT NULL,
    state_sha256 text NOT NULL CHECK (state_sha256 ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (execution_id, sequence),
    UNIQUE (execution_id, state_version),
    CHECK (sequence = state_version)
);

CREATE TABLE planning_checkpoint_test.step_receipts (
    execution_id uuid NOT NULL REFERENCES planning_checkpoint_test.executions(execution_id) ON DELETE CASCADE,
    step_id text NOT NULL,
    action_name text NOT NULL,
    idempotency_token text NOT NULL CHECK (idempotency_token ~ '^[0-9a-f]{64}$'),
    input_sha256 text NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    status text NOT NULL CHECK (status IN ('started','succeeded','failed','ambiguous')),
    output_json jsonb,
    output_sha256 text CHECK (output_sha256 IS NULL OR output_sha256 ~ '^[0-9a-f]{64}$'),
    tool_call_id text,
    error_code text,
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    PRIMARY KEY (execution_id, step_id),
    UNIQUE (execution_id, idempotency_token),
    CHECK (finished_at IS NULL OR finished_at >= started_at),
    CHECK (
        (status = 'started' AND output_json IS NULL AND output_sha256 IS NULL AND finished_at IS NULL)
        OR (status = 'succeeded' AND output_json IS NOT NULL AND output_sha256 IS NOT NULL AND finished_at IS NOT NULL AND error_code IS NULL)
        OR (status IN ('failed','ambiguous') AND output_json IS NULL AND output_sha256 IS NULL AND finished_at IS NOT NULL AND error_code IS NOT NULL)
    )
);

CREATE INDEX planning_checkpoint_test_execution_status
    ON planning_checkpoint_test.executions(status, lease_expires_at);

CREATE INDEX planning_checkpoint_test_receipt_status
    ON planning_checkpoint_test.step_receipts(execution_id, status);

REVOKE ALL ON ALL TABLES IN SCHEMA planning_checkpoint_test FROM PUBLIC;
