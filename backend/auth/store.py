"""PostgreSQL identity/session store. Opaque credentials are only persisted as digests."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import hmac
from typing import Callable
import uuid

import psycopg

from .models import AuthPrincipal, AuthServiceError, SessionRecord, UserRecord


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class PostgresAuthStore:
    def __init__(self, connection_factory: Callable):
        self._connections = connection_factory

    @contextmanager
    def _connection(self):
        try:
            with self._connections() as connection:
                yield connection
        except AuthServiceError:
            raise
        except (psycopg.Error, KeyError, OSError):
            raise AuthServiceError("auth_storage_unavailable") from None

    def create_user(
        self,
        *,
        account: str,
        display_name: str,
        password_hash: str,
        password_policy_version: str,
        now: datetime,
    ) -> UserRecord:
        user_id = uuid.uuid4()
        with self._connection() as connection, connection.transaction():
            try:
                row = connection.execute(
                    """INSERT INTO product_auth.users
                       (user_id,account_normalized,display_name,state,created_at,updated_at)
                       VALUES (%s,%s,%s,'active',%s,%s)
                       RETURNING user_id,account_normalized,display_name,state,created_at""",
                    (user_id, account, display_name, now, now),
                ).fetchone()
            except psycopg.errors.UniqueViolation:
                raise AuthServiceError("auth_registration_unavailable") from None
            connection.execute(
                """INSERT INTO product_auth.credentials
                   (user_id,password_hash,password_policy_version,password_changed_at)
                   VALUES (%s,%s,%s,%s)""",
                (user_id, password_hash, password_policy_version, now),
            )
            self._audit(connection, "register", "success", user_id, None, now)
        return self._user(row)

    def credential_for_account(self, account: str) -> tuple[UserRecord, str] | None:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT u.user_id,u.account_normalized,u.display_name,u.state,u.created_at,
                          c.password_hash
                   FROM product_auth.users u
                   JOIN product_auth.credentials c ON c.user_id=u.user_id
                   WHERE u.account_normalized=%s""",
                (account,),
            ).fetchone()
        if row is None:
            return None
        return self._user(row[:5]), row[5]

    def rate_limit_state(self, key_hash: str, now: datetime) -> datetime | None:
        with self._connection() as connection, connection.transaction():
            row = connection.execute(
                """SELECT blocked_until FROM product_auth.login_rate_limits
                   WHERE subject_hash=%s FOR UPDATE""",
                (key_hash,),
            ).fetchone()
            if row is None or row[0] is None or row[0] <= now:
                return None
            return row[0]

    def record_login_failure(
        self,
        *,
        key_hash: str,
        now: datetime,
        window_seconds: int,
        limit: int,
        block_seconds: int,
    ) -> bool:
        with self._connection() as connection, connection.transaction():
            row = connection.execute(
                """SELECT failure_count,window_started_at,blocked_until
                   FROM product_auth.login_rate_limits
                   WHERE subject_hash=%s FOR UPDATE""",
                (key_hash,),
            ).fetchone()
            from datetime import timedelta

            if row is None:
                count = 1
                window_started = now
            elif row[1] + timedelta(seconds=window_seconds) <= now:
                count = 1
                window_started = now
            else:
                count = row[0] + 1
                window_started = row[1]
            blocked_until = now + timedelta(seconds=block_seconds) if count >= limit else None
            connection.execute(
                """INSERT INTO product_auth.login_rate_limits
                   (subject_hash,failure_count,window_started_at,blocked_until,updated_at)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (subject_hash) DO UPDATE SET
                     failure_count=EXCLUDED.failure_count,
                     window_started_at=EXCLUDED.window_started_at,
                     blocked_until=EXCLUDED.blocked_until,
                     updated_at=EXCLUDED.updated_at""",
                (key_hash, count, window_started, blocked_until, now),
            )
            self._audit(connection, "login", "failure", None, None, now)
            return blocked_until is not None

    def clear_login_failures(self, key_hash: str) -> None:
        with self._connection() as connection, connection.transaction():
            connection.execute(
                "DELETE FROM product_auth.login_rate_limits WHERE subject_hash=%s",
                (key_hash,),
            )

    def record_login_success(self, *, user_id: uuid.UUID, now: datetime) -> None:
        with self._connection() as connection, connection.transaction():
            self._audit(connection, "login", "success", user_id, None, now)

    def create_session(
        self,
        *,
        user: UserRecord,
        token: str,
        csrf_token: str,
        now: datetime,
        expires_at: datetime,
    ) -> SessionRecord:
        session_id = uuid.uuid4()
        with self._connection() as connection, connection.transaction():
            row = connection.execute(
                """INSERT INTO product_auth.sessions
                   (session_id,user_id,token_hash,csrf_token_hash,state,created_at,expires_at,last_seen_at)
                   VALUES (%s,%s,%s,%s,'active',%s,%s,%s)
                   RETURNING session_id,user_id,state,created_at,expires_at,last_seen_at,revoked_at""",
                (session_id, user.user_id, _digest(token), _digest(csrf_token), now, expires_at, now),
            ).fetchone()
            self._audit(connection, "session_created", "success", user.user_id, session_id, now)
        return self._session(row)

    def resolve_session(
        self,
        *,
        token: str,
        now: datetime,
        touch: bool = True,
    ) -> tuple[UserRecord, SessionRecord, str] | None:
        token_hash = _digest(token)
        with self._connection() as connection, connection.transaction():
            connection.execute(
                """UPDATE product_auth.sessions SET state='expired'
                   WHERE token_hash=%s AND state='active' AND expires_at<=%s""",
                (token_hash, now),
            )
            row = connection.execute(
                """SELECT u.user_id,u.account_normalized,u.display_name,u.state,u.created_at,
                          s.session_id,s.user_id,s.state,s.created_at,s.expires_at,s.last_seen_at,
                          s.revoked_at,s.csrf_token_hash
                   FROM product_auth.sessions s
                   JOIN product_auth.users u ON u.user_id=s.user_id
                   WHERE s.token_hash=%s AND s.state='active' AND s.expires_at>%s
                         AND u.state='active'
                   FOR UPDATE OF s""",
                (token_hash, now),
            ).fetchone()
            if row is None:
                return None
            if touch:
                connection.execute(
                    "UPDATE product_auth.sessions SET last_seen_at=%s WHERE session_id=%s",
                    (now, row[5]),
                )
                row = (*row[:10], now, *row[11:])
        return self._user(row[:5]), self._session(row[5:12]), row[12]

    def revoke_session(self, *, token: str, now: datetime) -> bool:
        token_hash = _digest(token)
        with self._connection() as connection, connection.transaction():
            row = connection.execute(
                """UPDATE product_auth.sessions
                   SET state='revoked',revoked_at=%s,last_seen_at=%s
                   WHERE token_hash=%s AND state='active'
                   RETURNING user_id,session_id""",
                (now, now, token_hash),
            ).fetchone()
            if row is None:
                return False
            self._audit(connection, "logout", "success", row[0], row[1], now)
            return True

    def rotate_csrf(self, *, session_id: uuid.UUID, csrf_token: str, now: datetime) -> None:
        with self._connection() as connection, connection.transaction():
            updated = connection.execute(
                """UPDATE product_auth.sessions
                   SET csrf_token_hash=%s,last_seen_at=%s
                   WHERE session_id=%s AND state='active' AND expires_at>%s""",
                (_digest(csrf_token), now, session_id, now),
            ).rowcount
            if updated != 1:
                raise AuthServiceError("auth_required")

    def verify_csrf_hash(self, expected_hash: str, supplied_token: str) -> bool:
        return hmac.compare_digest(expected_hash, _digest(supplied_token))

    @staticmethod
    def _audit(connection, event_type, result, user_id, session_id, now):
        connection.execute(
            """INSERT INTO product_auth.audit_events
               (event_id,event_type,result,user_id,session_id,created_at)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (uuid.uuid4(), event_type, result, user_id, session_id, now),
        )

    @staticmethod
    def _user(row) -> UserRecord:
        return UserRecord(
            user_id=row[0], account=row[1], display_name=row[2], state=row[3], created_at=row[4]
        )

    @staticmethod
    def _session(row) -> SessionRecord:
        return SessionRecord(
            session_id=row[0], user_id=row[1], state=row[2], created_at=row[3],
            expires_at=row[4], last_seen_at=row[5], revoked_at=row[6],
        )
