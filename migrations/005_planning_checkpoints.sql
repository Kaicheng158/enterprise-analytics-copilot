CREATE SCHEMA agent_runtime;
REVOKE ALL ON SCHEMA agent_runtime FROM PUBLIC;

CREATE TABLE agent_runtime.executions (
    execution_id uuid PRIMARY KEY,
    scope_key text NOT NULL CHECK (length(scope_key) BETWEEN 1 AND 160),
    idempotency_key_hash text NOT NULL CHECK (idempotency_key_hash ~ '^[0-9a-f]{64}$'),
    request_sha256 text NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    plan_sha256 text NOT NULL CHECK (plan_sha256 ~ '^[0-9a-f]{64}$'),
    plan_version text NOT NULL CHECK (length(plan_version) BETWEEN 1 AND 80),
    plan_json jsonb NOT NULL,
    policy_version text NOT NULL CHECK (length(policy_version) BETWEEN 1 AND 80),
    runtime_version text NOT NULL CHECK (length(runtime_version) BETWEEN 1 AND 80),
    status text NOT NULL CHECK (status IN ('pending','running','reconciling','completed','failed')),
    state_version bigint NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    checkpoint_sequence bigint NOT NULL DEFAULT 0 CHECK (checkpoint_sequence >= 0),
    lease_owner text CHECK (lease_owner IS NULL OR length(lease_owner) BETWEEN 1 AND 128),
    lease_expires_at timestamptz,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    UNIQUE (scope_key, idempotency_key_hash),
    CHECK ((lease_owner IS NULL) = (lease_expires_at IS NULL)),
    CHECK (state_version = checkpoint_sequence),
    CHECK (updated_at >= created_at)
);

CREATE TABLE agent_runtime.checkpoints (
    execution_id uuid NOT NULL REFERENCES agent_runtime.executions(execution_id) ON DELETE CASCADE,
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

CREATE TABLE agent_runtime.step_receipts (
    execution_id uuid NOT NULL REFERENCES agent_runtime.executions(execution_id) ON DELETE CASCADE,
    step_id text NOT NULL CHECK (step_id ~ '^[a-z][a-z0-9_]{0,63}$'),
    action_name text NOT NULL CHECK (length(action_name) BETWEEN 1 AND 100),
    idempotency_token text NOT NULL CHECK (idempotency_token ~ '^[0-9a-f]{64}$'),
    input_sha256 text NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    status text NOT NULL CHECK (status IN ('started','succeeded','failed','ambiguous')),
    output_json jsonb,
    output_sha256 text CHECK (output_sha256 IS NULL OR output_sha256 ~ '^[0-9a-f]{64}$'),
    tool_call_id text CHECK (tool_call_id IS NULL OR length(tool_call_id) <= 160),
    error_code text CHECK (error_code IS NULL OR length(error_code) <= 100),
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    PRIMARY KEY (execution_id, step_id),
    UNIQUE (execution_id, idempotency_token),
    CHECK (finished_at IS NULL OR finished_at >= started_at),
    CHECK (
        (status = 'started' AND output_json IS NULL AND output_sha256 IS NULL AND finished_at IS NULL AND error_code IS NULL)
        OR (status = 'succeeded' AND output_json IS NOT NULL AND output_sha256 IS NOT NULL AND finished_at IS NOT NULL AND error_code IS NULL)
        OR (status IN ('failed','ambiguous') AND output_json IS NULL AND output_sha256 IS NULL AND finished_at IS NOT NULL AND error_code IS NOT NULL)
    )
);

CREATE INDEX agent_runtime_execution_status_lease
    ON agent_runtime.executions(status, lease_expires_at);
CREATE INDEX agent_runtime_receipt_status
    ON agent_runtime.step_receipts(execution_id, status);
CREATE INDEX agent_runtime_checkpoint_latest
    ON agent_runtime.checkpoints(execution_id, sequence DESC);

REVOKE ALL ON ALL TABLES IN SCHEMA agent_runtime FROM PUBLIC;
