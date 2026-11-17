CREATE TABLE product_auth.project_owners (
    project_id uuid PRIMARY KEY REFERENCES dataset_store.projects(project_id),
    owner_user_id uuid NOT NULL REFERENCES product_auth.users(user_id),
    state text NOT NULL CHECK (state IN ('active','revoked')),
    authorization_version bigint NOT NULL DEFAULT 1 CHECK (authorization_version > 0),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    revoked_at timestamptz,
    UNIQUE (project_id, owner_user_id),
    CHECK ((state='revoked') = (revoked_at IS NOT NULL)),
    CHECK (updated_at >= created_at)
);

CREATE TABLE product_auth.project_task_bindings (
    execution_id uuid PRIMARY KEY REFERENCES agent_runtime.executions(execution_id) ON DELETE CASCADE,
    project_id uuid NOT NULL REFERENCES product_auth.project_owners(project_id),
    owner_user_id uuid NOT NULL REFERENCES product_auth.users(user_id),
    authorization_version bigint NOT NULL CHECK (authorization_version > 0),
    state text NOT NULL CHECK (state IN ('active','revoked')),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    revoked_at timestamptz,
    FOREIGN KEY (project_id, owner_user_id)
        REFERENCES product_auth.project_owners(project_id, owner_user_id),
    CHECK ((state='revoked') = (revoked_at IS NOT NULL)),
    CHECK (updated_at >= created_at)
);

CREATE TABLE product_auth.authorization_audit_events (
    event_id uuid PRIMARY KEY,
    actor_user_id uuid REFERENCES product_auth.users(user_id) ON DELETE SET NULL,
    session_id uuid REFERENCES product_auth.sessions(session_id) ON DELETE SET NULL,
    project_id uuid,
    asset_id uuid,
    execution_id uuid,
    action text NOT NULL CHECK (action ~ '^[a-z][a-z0-9_]{1,79}$'),
    decision text NOT NULL CHECK (decision IN ('allowed','denied','revoked')),
    reason_code text NOT NULL CHECK (reason_code ~ '^[a-z][a-z0-9_]{1,79}$'),
    authorization_version bigint CHECK (authorization_version IS NULL OR authorization_version > 0),
    created_at timestamptz NOT NULL,
    CHECK (asset_id IS NULL OR project_id IS NOT NULL),
    CHECK (execution_id IS NULL OR project_id IS NOT NULL)
);

CREATE INDEX project_owners_user_state
    ON product_auth.project_owners(owner_user_id, state, project_id);
CREATE INDEX project_task_bindings_project_state
    ON product_auth.project_task_bindings(project_id, state, execution_id);
CREATE INDEX authorization_audit_project_created
    ON product_auth.authorization_audit_events(project_id, created_at);

CREATE FUNCTION product_auth.reject_revoked_task_write()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM product_auth.project_task_bindings binding
        JOIN product_auth.project_owners owner
          ON owner.project_id=binding.project_id
         AND owner.owner_user_id=binding.owner_user_id
        JOIN product_auth.users users
          ON users.user_id=owner.owner_user_id
        WHERE binding.execution_id=NEW.execution_id
          AND (
              binding.state <> 'active'
              OR owner.state <> 'active'
              OR users.state <> 'active'
              OR binding.authorization_version <> owner.authorization_version
          )
    ) THEN
        RAISE EXCEPTION 'project task authorization revoked'
            USING ERRCODE='42501';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER checkpoint_project_authorization_guard
BEFORE INSERT OR UPDATE ON agent_runtime.checkpoints
FOR EACH ROW EXECUTE FUNCTION product_auth.reject_revoked_task_write();

CREATE TRIGGER receipt_project_authorization_guard
BEFORE INSERT OR UPDATE ON agent_runtime.step_receipts
FOR EACH ROW EXECUTE FUNCTION product_auth.reject_revoked_task_write();

REVOKE ALL ON product_auth.project_owners FROM PUBLIC;
REVOKE ALL ON product_auth.project_task_bindings FROM PUBLIC;
REVOKE ALL ON product_auth.authorization_audit_events FROM PUBLIC;
REVOKE ALL ON FUNCTION product_auth.reject_revoked_task_write() FROM PUBLIC;
