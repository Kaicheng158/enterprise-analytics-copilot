CREATE SCHEMA product_facade;
REVOKE ALL ON SCHEMA product_facade FROM PUBLIC;

CREATE TABLE product_facade.project_profiles (
    project_id uuid PRIMARY KEY REFERENCES dataset_store.projects(project_id) ON DELETE CASCADE,
    owner_user_id uuid NOT NULL REFERENCES product_auth.users(user_id),
    display_name text NOT NULL CHECK (
        length(display_name) BETWEEN 1 AND 120
        AND display_name = btrim(display_name)
    ),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (updated_at >= created_at)
);

CREATE TABLE product_facade.asset_profiles (
    project_id uuid NOT NULL,
    asset_id uuid NOT NULL,
    display_name text NOT NULL CHECK (
        length(display_name) BETWEEN 1 AND 120
        AND display_name = btrim(display_name)
    ),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, asset_id),
    FOREIGN KEY (project_id, asset_id)
        REFERENCES dataset_store.assets(project_id, asset_id) ON DELETE CASCADE,
    CHECK (updated_at >= created_at)
);

CREATE TABLE product_facade.asset_revision_profiles (
    project_id uuid NOT NULL,
    asset_revision_id uuid NOT NULL,
    original_filename text NOT NULL CHECK (
        length(original_filename) BETWEEN 1 AND 128
        AND original_filename = btrim(original_filename)
    ),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, asset_revision_id),
    FOREIGN KEY (project_id, asset_revision_id)
        REFERENCES dataset_store.asset_revisions(project_id, revision_id) ON DELETE CASCADE
);

CREATE TABLE product_facade.write_idempotency (
    owner_user_id uuid NOT NULL REFERENCES product_auth.users(user_id),
    operation text NOT NULL CHECK (operation IN ('project_create','dataset_upload')),
    key_hash text NOT NULL CHECK (key_hash ~ '^[0-9a-f]{64}$'),
    request_sha256 text NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    project_id uuid NOT NULL REFERENCES dataset_store.projects(project_id) ON DELETE CASCADE,
    asset_id uuid,
    asset_revision_id uuid,
    dataset_revision_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_user_id, operation, key_hash),
    FOREIGN KEY (project_id, asset_id)
        REFERENCES dataset_store.assets(project_id, asset_id),
    FOREIGN KEY (project_id, asset_revision_id)
        REFERENCES dataset_store.asset_revisions(project_id, revision_id),
    FOREIGN KEY (project_id, dataset_revision_id)
        REFERENCES dataset_store.dataset_revisions(project_id, dataset_revision_id),
    CHECK (
        (operation = 'project_create'
         AND asset_id IS NULL
         AND asset_revision_id IS NULL
         AND dataset_revision_id IS NULL)
        OR
        (operation = 'dataset_upload'
         AND asset_id IS NOT NULL
         AND asset_revision_id IS NOT NULL
         AND dataset_revision_id IS NOT NULL)
    )
);

CREATE INDEX product_facade_project_profiles_owner
    ON product_facade.project_profiles(owner_user_id, project_id);
CREATE INDEX product_facade_asset_profiles_project
    ON product_facade.asset_profiles(project_id, created_at, asset_id);
CREATE INDEX product_facade_asset_revision_profiles_project
    ON product_facade.asset_revision_profiles(project_id, created_at, asset_revision_id);
CREATE INDEX product_facade_idempotency_project
    ON product_facade.write_idempotency(project_id, created_at);

REVOKE ALL ON ALL TABLES IN SCHEMA product_facade FROM PUBLIC;
