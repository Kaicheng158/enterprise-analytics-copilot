"""Fail-closed project, asset and task authorization for authenticated users."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Callable
from uuid import UUID, uuid4

import psycopg

from backend.auth.models import AuthPrincipal

from .models import (
    AuthorizationFailure,
    AuthorizedResource,
    ProjectAction,
    ProjectAuthorizationSettings,
    ProjectOwner,
    ProjectTaskBinding,
)


def project_scope_key(project_id: UUID) -> str:
    if type(project_id) is not UUID:
        raise AuthorizationFailure("authorization_contract_invalid")
    return f"product-project:{project_id}"


class ProjectAuthorizationService:
    """Authorization derives from a live server session, never request identity fields."""

    def __init__(
        self,
        connection_factory: Callable,
        *,
        settings: ProjectAuthorizationSettings | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self._connections = connection_factory
        self._settings = settings or ProjectAuthorizationSettings()
        self._clock = clock or (lambda: datetime.now(UTC))

    def _enabled(self) -> None:
        if not self._settings.enabled:
            raise AuthorizationFailure("authorization_disabled")

    @contextmanager
    def _connection(self):
        try:
            with self._connections() as connection:
                yield connection
        except AuthorizationFailure:
            raise
        except (psycopg.Error, KeyError, OSError):
            raise AuthorizationFailure("authorization_unavailable") from None

    @staticmethod
    def _validate_contract(principal: AuthPrincipal, project_id: UUID) -> None:
        if type(principal) is not AuthPrincipal or type(project_id) is not UUID:
            raise AuthorizationFailure("authorization_contract_invalid")

    @staticmethod
    def _principal_user(connection, principal: AuthPrincipal, now: datetime) -> UUID | None:
        row = connection.execute(
            """SELECT users.user_id
               FROM product_auth.users users
               JOIN product_auth.sessions sessions ON sessions.user_id=users.user_id
               WHERE users.user_id=%s AND users.state='active'
                 AND sessions.session_id=%s AND sessions.state='active'
                 AND sessions.expires_at>%s""",
            (principal.user_id, principal.session_id, now),
        ).fetchone()
        return None if row is None else row[0]

    @staticmethod
    def _audit(
        connection,
        *,
        principal: AuthPrincipal,
        project_id: UUID,
        action: ProjectAction,
        decision: str,
        reason_code: str,
        now: datetime,
        actor_user_id: UUID | None,
        authorization_version: int | None = None,
        asset_id: UUID | None = None,
        execution_id: UUID | None = None,
    ) -> None:
        connection.execute(
            """INSERT INTO product_auth.authorization_audit_events
               (event_id,actor_user_id,session_id,project_id,asset_id,execution_id,
                action,decision,reason_code,authorization_version,created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                uuid4(), actor_user_id,
                principal.session_id if actor_user_id is not None else None,
                project_id, asset_id, execution_id, action, decision, reason_code,
                authorization_version, now,
            ),
        )

    def create_owned_project(
        self,
        principal: AuthPrincipal,
        *,
        project_id: UUID,
        quota_bytes: int,
        lifecycle_policy_version: str,
    ) -> ProjectOwner:
        self._enabled()
        self._validate_contract(principal, project_id)
        if type(quota_bytes) is not int or quota_bytes <= 0:
            raise AuthorizationFailure("authorization_contract_invalid")
        if not isinstance(lifecycle_policy_version, str) or not 1 <= len(lifecycle_policy_version) <= 80:
            raise AuthorizationFailure("authorization_contract_invalid")
        now = self._clock()
        with self._connection() as connection, connection.transaction():
            actor = self._principal_user(connection, principal, now)
            if actor is None:
                raise AuthorizationFailure()
            connection.execute(
                """INSERT INTO dataset_store.projects
                   (project_id,scope_key,quota_bytes,lifecycle_policy_version)
                   VALUES (%s,%s,%s,%s)""",
                (project_id, project_scope_key(project_id), quota_bytes, lifecycle_policy_version),
            )
            row = connection.execute(
                """INSERT INTO product_auth.project_owners
                   (project_id,owner_user_id,state,authorization_version,created_at,updated_at)
                   VALUES (%s,%s,'active',1,%s,%s)
                   RETURNING project_id,owner_user_id,state,authorization_version,
                             created_at,updated_at,revoked_at""",
                (project_id, actor, now, now),
            ).fetchone()
            self._audit(
                connection, principal=principal, project_id=project_id,
                action="project_write", decision="allowed", reason_code="owner_created",
                now=now, actor_user_id=actor, authorization_version=1,
            )
        return self._owner(row)

    def require_project(
        self,
        principal: AuthPrincipal,
        project_id: UUID,
        *,
        action: ProjectAction,
    ) -> AuthorizedResource:
        if action not in {"project_read", "project_write"}:
            raise AuthorizationFailure("authorization_contract_invalid")
        return self._require(principal, project_id, action=action)

    def require_asset(
        self,
        principal: AuthPrincipal,
        project_id: UUID,
        asset_id: UUID,
        *,
        action: ProjectAction,
    ) -> AuthorizedResource:
        if type(asset_id) is not UUID or action not in {"asset_read", "asset_write"}:
            raise AuthorizationFailure("authorization_contract_invalid")
        return self._require(principal, project_id, action=action, asset_id=asset_id)

    def require_task(
        self,
        principal: AuthPrincipal,
        project_id: UUID,
        execution_id: UUID,
        *,
        action: ProjectAction = "task_read",
    ) -> AuthorizedResource:
        if type(execution_id) is not UUID or action not in {"task_read", "task_execute"}:
            raise AuthorizationFailure("authorization_contract_invalid")
        return self._require(principal, project_id, action=action, execution_id=execution_id)

    def _require(
        self,
        principal: AuthPrincipal,
        project_id: UUID,
        *,
        action: ProjectAction,
        asset_id: UUID | None = None,
        execution_id: UUID | None = None,
    ) -> AuthorizedResource:
        self._enabled()
        self._validate_contract(principal, project_id)
        now = self._clock()
        with self._connection() as connection, connection.transaction():
            actor = self._principal_user(connection, principal, now)
            row = None
            if actor is not None:
                row = connection.execute(
                    """SELECT owner.authorization_version
                       FROM product_auth.project_owners owner
                       JOIN dataset_store.projects project ON project.project_id=owner.project_id
                       WHERE owner.project_id=%s AND owner.owner_user_id=%s
                         AND owner.state='active' AND project.state='active'""",
                    (project_id, actor),
                ).fetchone()
                if row is not None and asset_id is not None:
                    exists = connection.execute(
                        """SELECT 1 FROM dataset_store.assets
                           WHERE project_id=%s AND asset_id=%s AND state='active'""",
                        (project_id, asset_id),
                    ).fetchone()
                    row = row if exists is not None else None
                if row is not None and execution_id is not None:
                    exists = connection.execute(
                        """SELECT 1 FROM product_auth.project_task_bindings
                           WHERE execution_id=%s AND project_id=%s AND owner_user_id=%s
                             AND state='active' AND authorization_version=%s""",
                        (execution_id, project_id, actor, row[0]),
                    ).fetchone()
                    row = row if exists is not None else None
            allowed = row is not None
            version = row[0] if allowed else None
            self._audit(
                connection, principal=principal, project_id=project_id,
                asset_id=asset_id, execution_id=execution_id, action=action,
                decision="allowed" if allowed else "denied",
                reason_code="owner_authorized" if allowed else "authorization_denied",
                now=now, actor_user_id=actor, authorization_version=version,
            )
        if not allowed:
            raise AuthorizationFailure()
        return AuthorizedResource(
            user_id=principal.user_id, project_id=project_id, asset_id=asset_id,
            execution_id=execution_id, action=action, authorization_version=version,
        )

    def bind_task(
        self,
        principal: AuthPrincipal,
        project_id: UUID,
        execution_id: UUID,
    ) -> ProjectTaskBinding:
        self._enabled()
        self._validate_contract(principal, project_id)
        if type(execution_id) is not UUID:
            raise AuthorizationFailure("authorization_contract_invalid")
        now = self._clock()
        with self._connection() as connection, connection.transaction():
            actor = self._principal_user(connection, principal, now)
            owner = None if actor is None else connection.execute(
                """SELECT authorization_version FROM product_auth.project_owners
                   WHERE project_id=%s AND owner_user_id=%s AND state='active' FOR UPDATE""",
                (project_id, actor),
            ).fetchone()
            execution = connection.execute(
                "SELECT scope_key FROM agent_runtime.executions WHERE execution_id=%s",
                (execution_id,),
            ).fetchone()
            if owner is None or execution is None or execution[0] != project_scope_key(project_id):
                self._audit(
                    connection, principal=principal, project_id=project_id,
                    execution_id=execution_id, action="task_submit", decision="denied",
                    reason_code="authorization_denied", now=now, actor_user_id=actor,
                )
                denied = True
                row = None
            else:
                row = connection.execute(
                    """INSERT INTO product_auth.project_task_bindings
                       (execution_id,project_id,owner_user_id,authorization_version,
                        state,created_at,updated_at)
                       VALUES (%s,%s,%s,%s,'active',%s,%s)
                       ON CONFLICT (execution_id) DO UPDATE SET updated_at=EXCLUDED.updated_at
                       WHERE product_auth.project_task_bindings.project_id=EXCLUDED.project_id
                         AND product_auth.project_task_bindings.owner_user_id=EXCLUDED.owner_user_id
                         AND product_auth.project_task_bindings.authorization_version=EXCLUDED.authorization_version
                         AND product_auth.project_task_bindings.state='active'
                       RETURNING execution_id,project_id,owner_user_id,authorization_version,
                                 state,created_at,updated_at,revoked_at""",
                    (execution_id, project_id, actor, owner[0], now, now),
                ).fetchone()
                denied = row is None
                self._audit(
                    connection, principal=principal, project_id=project_id,
                    execution_id=execution_id, action="task_submit",
                    decision="denied" if denied else "allowed",
                    reason_code="authorization_denied" if denied else "task_bound",
                    now=now, actor_user_id=actor,
                    authorization_version=None if denied else owner[0],
                )
        if denied:
            raise AuthorizationFailure()
        return self._binding(row)

    def revoke_project(self, principal: AuthPrincipal, project_id: UUID) -> ProjectOwner:
        self._enabled()
        self._validate_contract(principal, project_id)
        now = self._clock()
        with self._connection() as connection, connection.transaction():
            actor = self._principal_user(connection, principal, now)
            row = None if actor is None else connection.execute(
                """UPDATE product_auth.project_owners
                   SET state='revoked',authorization_version=authorization_version+1,
                       updated_at=%s,revoked_at=%s
                   WHERE project_id=%s AND owner_user_id=%s AND state='active'
                   RETURNING project_id,owner_user_id,state,authorization_version,
                             created_at,updated_at,revoked_at""",
                (now, now, project_id, actor),
            ).fetchone()
            if row is None:
                self._audit(
                    connection, principal=principal, project_id=project_id,
                    action="project_revoke", decision="denied",
                    reason_code="authorization_denied", now=now, actor_user_id=actor,
                )
                denied = True
            else:
                connection.execute(
                    """UPDATE product_auth.project_task_bindings
                       SET state='revoked',updated_at=%s,revoked_at=%s
                       WHERE project_id=%s AND state='active'""",
                    (now, now, project_id),
                )
                self._audit(
                    connection, principal=principal, project_id=project_id,
                    action="project_revoke", decision="revoked",
                    reason_code="owner_revoked", now=now, actor_user_id=actor,
                    authorization_version=row[3],
                )
                denied = False
        if denied:
            raise AuthorizationFailure()
        return self._owner(row)

    def require_task_execution(self, execution_id: UUID) -> ProjectTaskBinding:
        """Worker-side guard; no browser/user identity can invoke this as authorization."""
        self._enabled()
        if type(execution_id) is not UUID:
            raise AuthorizationFailure("authorization_contract_invalid")
        now = self._clock()
        with self._connection() as connection:
            row = connection.execute(
                """SELECT binding.execution_id,binding.project_id,binding.owner_user_id,
                          binding.authorization_version,binding.state,binding.created_at,
                          binding.updated_at,binding.revoked_at
                   FROM product_auth.project_task_bindings binding
                   JOIN product_auth.project_owners owner
                     ON owner.project_id=binding.project_id
                    AND owner.owner_user_id=binding.owner_user_id
                   JOIN product_auth.users users ON users.user_id=owner.owner_user_id
                   WHERE binding.execution_id=%s AND binding.state='active'
                     AND owner.state='active' AND users.state='active'
                     AND binding.authorization_version=owner.authorization_version""",
                (execution_id,),
            ).fetchone()
        if row is None:
            raise AuthorizationFailure()
        return self._binding(row)

    @staticmethod
    def _owner(row) -> ProjectOwner:
        return ProjectOwner(
            project_id=row[0], owner_user_id=row[1], state=row[2],
            authorization_version=row[3], created_at=row[4], updated_at=row[5],
            revoked_at=row[6],
        )

    @staticmethod
    def _binding(row) -> ProjectTaskBinding:
        return ProjectTaskBinding(
            execution_id=row[0], project_id=row[1], owner_user_id=row[2],
            authorization_version=row[3], state=row[4], created_at=row[5],
            updated_at=row[6], revoked_at=row[7],
        )
