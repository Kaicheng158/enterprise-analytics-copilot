ALTER TABLE dataset_store.storage_operations
    ADD COLUMN state_version bigint NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    ADD COLUMN reconcile_after timestamptz;

UPDATE dataset_store.storage_operations
SET reconcile_after = updated_at + interval '15 minutes'
WHERE state IN ('reserved','writing','reconciling');

CREATE TABLE dataset_store.lifecycle_operations (
    operation_id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES dataset_store.projects(project_id),
    asset_id uuid NOT NULL,
    asset_revision_id uuid NOT NULL,
    action text NOT NULL CHECK (action = 'delete_revision'),
    state text NOT NULL CHECK (state IN ('marked','removing','reconciling','completed')),
    logical_bytes bigint NOT NULL CHECK (logical_bytes >= 0),
    quota_released_bytes bigint NOT NULL DEFAULT 0 CHECK (quota_released_bytes >= 0),
    lease_owner uuid,
    lease_expires_at timestamptz,
    fence_token bigint NOT NULL DEFAULT 0 CHECK (fence_token >= 0),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_error_code text CHECK (last_error_code IS NULL OR last_error_code ~ '^[a-z][a-z0-9_]{0,79}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    UNIQUE (project_id, asset_revision_id, action),
    FOREIGN KEY (project_id, asset_id) REFERENCES dataset_store.assets(project_id, asset_id),
    FOREIGN KEY (project_id, asset_revision_id) REFERENCES dataset_store.asset_revisions(project_id, revision_id),
    CHECK ((lease_owner IS NULL) = (lease_expires_at IS NULL)),
    CHECK ((state = 'completed') = (completed_at IS NOT NULL)),
    CHECK (state <> 'completed' OR quota_released_bytes = logical_bytes),
    CHECK (quota_released_bytes <= logical_bytes),
    CHECK (updated_at >= created_at)
);

CREATE TABLE dataset_store.lifecycle_audit_events (
    event_id uuid PRIMARY KEY,
    run_id uuid,
    project_id uuid,
    asset_revision_id uuid,
    operation_id uuid,
    event_type text NOT NULL CHECK (event_type ~ '^[a-z][a-z0-9_]{0,79}$'),
    result text NOT NULL CHECK (result IN ('started','completed','deferred','failed')),
    item_count integer NOT NULL DEFAULT 0 CHECK (item_count >= 0),
    logical_bytes bigint NOT NULL DEFAULT 0 CHECK (logical_bytes >= 0),
    physical_bytes bigint NOT NULL DEFAULT 0 CHECK (physical_bytes >= 0),
    error_code text CHECK (error_code IS NULL OR error_code ~ '^[a-z][a-z0-9_]{0,79}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (result <> 'failed' OR error_code IS NOT NULL)
);

CREATE INDEX dataset_store_lifecycle_operation_state
    ON dataset_store.lifecycle_operations(state, lease_expires_at, updated_at);
CREATE INDEX dataset_store_revision_retention
    ON dataset_store.asset_revisions(state, retention_until)
    WHERE retention_until IS NOT NULL;
CREATE INDEX dataset_store_storage_reconcile_due
    ON dataset_store.storage_operations(state, reconcile_after)
    WHERE state IN ('reserved','writing','reconciling');
CREATE INDEX dataset_store_lifecycle_audit_run
    ON dataset_store.lifecycle_audit_events(run_id, created_at);

REVOKE ALL ON ALL TABLES IN SCHEMA dataset_store FROM PUBLIC;
