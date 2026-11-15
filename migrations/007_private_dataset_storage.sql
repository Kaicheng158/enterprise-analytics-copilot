CREATE SCHEMA dataset_store;
REVOKE ALL ON SCHEMA dataset_store FROM PUBLIC;

CREATE TABLE dataset_store.projects (
    project_id uuid PRIMARY KEY,
    scope_key text NOT NULL CHECK (length(scope_key) BETWEEN 1 AND 160),
    state text NOT NULL DEFAULT 'active' CHECK (state IN ('active','archived','deleting','deleted')),
    quota_bytes bigint NOT NULL CHECK (quota_bytes > 0),
    used_bytes bigint NOT NULL DEFAULT 0 CHECK (used_bytes >= 0),
    reserved_bytes bigint NOT NULL DEFAULT 0 CHECK (reserved_bytes >= 0),
    lifecycle_policy_version text NOT NULL CHECK (length(lifecycle_policy_version) BETWEEN 1 AND 80),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_key, project_id),
    CHECK (used_bytes + reserved_bytes <= quota_bytes),
    CHECK (updated_at >= created_at)
);

CREATE TABLE dataset_store.assets (
    project_id uuid NOT NULL REFERENCES dataset_store.projects(project_id),
    asset_id uuid NOT NULL,
    asset_key text NOT NULL CHECK (asset_key ~ '^[a-z][a-z0-9_-]{0,63}$'),
    state text NOT NULL DEFAULT 'active' CHECK (state IN ('active','expired','deleting','deleted')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, asset_id),
    UNIQUE (project_id, asset_key)
);

CREATE TABLE dataset_store.asset_revisions (
    project_id uuid NOT NULL,
    asset_id uuid NOT NULL,
    revision_id uuid NOT NULL,
    revision_number bigint NOT NULL CHECK (revision_number > 0),
    source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    normalized_sha256 text NOT NULL CHECK (normalized_sha256 ~ '^[0-9a-f]{64}$'),
    parse_contract_sha256 text NOT NULL CHECK (parse_contract_sha256 ~ '^[0-9a-f]{64}$'),
    source_format text NOT NULL CHECK (source_format IN ('csv','xlsx')),
    source_bytes bigint NOT NULL CHECK (source_bytes >= 0),
    storage_ref text CHECK (storage_ref IS NULL OR storage_ref ~ '^private:v1:[0-9a-f]{32}:raw$'),
    state text NOT NULL CHECK (state IN ('pending','ready','reconciling','failed','expired','deleting','deleted')),
    parser_policy_version text NOT NULL CHECK (length(parser_policy_version) BETWEEN 1 AND 80),
    lifecycle_policy_version text NOT NULL CHECK (length(lifecycle_policy_version) BETWEEN 1 AND 80),
    retention_until timestamptz,
    deletion_marked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    ready_at timestamptz,
    PRIMARY KEY (project_id, revision_id),
    UNIQUE (project_id, asset_id, revision_number),
    UNIQUE (project_id, source_sha256, parse_contract_sha256),
    FOREIGN KEY (project_id, asset_id) REFERENCES dataset_store.assets(project_id, asset_id),
    CHECK ((state = 'ready') = (storage_ref IS NOT NULL AND ready_at IS NOT NULL)),
    CHECK (deletion_marked_at IS NULL OR state IN ('deleting','deleted'))
);

CREATE TABLE dataset_store.dataset_revisions (
    project_id uuid NOT NULL,
    dataset_revision_id uuid NOT NULL,
    asset_revision_id uuid NOT NULL,
    normalized_sha256 text NOT NULL CHECK (normalized_sha256 ~ '^[0-9a-f]{64}$'),
    blob_sha256 text NOT NULL CHECK (blob_sha256 ~ '^[0-9a-f]{64}$'),
    dataset_contract_version text NOT NULL CHECK (length(dataset_contract_version) BETWEEN 1 AND 80),
    parser_policy_version text NOT NULL CHECK (length(parser_policy_version) BETWEEN 1 AND 80),
    dataset_bytes bigint NOT NULL CHECK (dataset_bytes >= 0),
    storage_ref text CHECK (storage_ref IS NULL OR storage_ref ~ '^private:v1:[0-9a-f]{32}:dataset$'),
    state text NOT NULL CHECK (state IN ('pending','ready','reconciling','failed','expired','deleting','deleted')),
    created_at timestamptz NOT NULL DEFAULT now(),
    ready_at timestamptz,
    PRIMARY KEY (project_id, dataset_revision_id),
    UNIQUE (project_id, asset_revision_id),
    FOREIGN KEY (project_id, asset_revision_id) REFERENCES dataset_store.asset_revisions(project_id, revision_id),
    CHECK ((state = 'ready') = (storage_ref IS NOT NULL AND ready_at IS NOT NULL))
);

CREATE TABLE dataset_store.storage_operations (
    operation_id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES dataset_store.projects(project_id),
    asset_id uuid NOT NULL,
    asset_revision_id uuid NOT NULL,
    request_sha256 text NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    state text NOT NULL CHECK (state IN ('reserved','writing','ready','reconciling','failed')),
    reserved_bytes bigint NOT NULL CHECK (reserved_bytes >= 0),
    actual_bytes bigint CHECK (actual_bytes IS NULL OR actual_bytes >= 0),
    error_code text CHECK (error_code IS NULL OR error_code ~ '^[a-z][a-z0-9_]{0,79}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, asset_id, request_sha256),
    FOREIGN KEY (project_id, asset_id) REFERENCES dataset_store.assets(project_id, asset_id),
    FOREIGN KEY (project_id, asset_revision_id) REFERENCES dataset_store.asset_revisions(project_id, revision_id),
    CHECK (updated_at >= created_at),
    CHECK ((state IN ('reserved','writing','reconciling')) = (reserved_bytes > 0)),
    CHECK ((state = 'ready') = (actual_bytes IS NOT NULL AND error_code IS NULL)),
    CHECK (state <> 'failed' OR error_code IS NOT NULL)
);

CREATE INDEX dataset_store_revision_state ON dataset_store.asset_revisions(project_id, state, created_at);
CREATE INDEX dataset_store_operation_state ON dataset_store.storage_operations(state, updated_at);

REVOKE ALL ON ALL TABLES IN SCHEMA dataset_store FROM PUBLIC;
