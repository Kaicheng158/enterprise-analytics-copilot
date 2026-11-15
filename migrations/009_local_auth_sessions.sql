CREATE SCHEMA product_auth;
REVOKE ALL ON SCHEMA product_auth FROM PUBLIC;

CREATE TABLE product_auth.users (
    user_id uuid PRIMARY KEY,
    account_normalized text NOT NULL UNIQUE
        CHECK (length(account_normalized) BETWEEN 3 AND 254),
    display_name text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 80),
    state text NOT NULL CHECK (state IN ('active','disabled')),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL CHECK (updated_at >= created_at)
);

CREATE TABLE product_auth.credentials (
    user_id uuid PRIMARY KEY REFERENCES product_auth.users(user_id) ON DELETE CASCADE,
    password_hash text NOT NULL CHECK (length(password_hash) BETWEEN 40 AND 512),
    password_policy_version text NOT NULL
        CHECK (length(password_policy_version) BETWEEN 1 AND 40),
    password_changed_at timestamptz NOT NULL
);

CREATE TABLE product_auth.sessions (
    session_id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES product_auth.users(user_id) ON DELETE CASCADE,
    token_hash text NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    csrf_token_hash text NOT NULL CHECK (csrf_token_hash ~ '^[0-9a-f]{64}$'),
    state text NOT NULL CHECK (state IN ('active','revoked','expired')),
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL CHECK (expires_at > created_at),
    last_seen_at timestamptz NOT NULL CHECK (last_seen_at >= created_at),
    revoked_at timestamptz,
    CHECK ((state='revoked') = (revoked_at IS NOT NULL))
);
CREATE INDEX auth_sessions_user_state ON product_auth.sessions(user_id,state,expires_at);

CREATE TABLE product_auth.login_rate_limits (
    subject_hash text PRIMARY KEY CHECK (subject_hash ~ '^[0-9a-f]{64}$'),
    failure_count integer NOT NULL CHECK (failure_count > 0),
    window_started_at timestamptz NOT NULL,
    blocked_until timestamptz,
    updated_at timestamptz NOT NULL CHECK (updated_at >= window_started_at)
);

CREATE TABLE product_auth.audit_events (
    event_id uuid PRIMARY KEY,
    event_type text NOT NULL CHECK (event_type IN ('register','login','session_created','logout')),
    result text NOT NULL CHECK (result IN ('success','failure')),
    user_id uuid REFERENCES product_auth.users(user_id) ON DELETE SET NULL,
    session_id uuid REFERENCES product_auth.sessions(session_id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL
);
CREATE INDEX auth_audit_created ON product_auth.audit_events(created_at,event_type);

REVOKE ALL ON ALL TABLES IN SCHEMA product_auth FROM PUBLIC;
