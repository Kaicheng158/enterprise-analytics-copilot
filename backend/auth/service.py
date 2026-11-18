"""Authentication orchestration with generic failures, rate limits and opaque sessions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import secrets
from typing import Callable

from .models import (
    AuthPrincipal,
    AuthServiceError,
    LoginRequest,
    RegisterRequest,
    SessionView,
    WorkspaceIdentity,
)
from .passwords import Argon2PasswordService
from .settings import LocalAuthSettings
from .store import PostgresAuthStore


class LocalAuthService:
    def __init__(
        self,
        store: PostgresAuthStore,
        *,
        settings: LocalAuthSettings,
        passwords: Argon2PasswordService | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self._store = store
        self._settings = settings
        self._passwords = passwords or Argon2PasswordService()
        self._clock = clock or (lambda: datetime.now(UTC))

    def _require_enabled(self) -> None:
        if not self._settings.enabled:
            raise AuthServiceError("auth_disabled")

    def _rate_key(self, account: str, client_address: str) -> str:
        secret = self._settings.rate_limit_secret.get_secret_value().encode()
        material = f"{account}\0{client_address}".encode()
        return hmac.new(secret, material, hashlib.sha256).hexdigest()

    def _new_session(self, user) -> tuple[SessionView, str]:
        now = self._clock()
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        expires_at = now + timedelta(seconds=self._settings.session_ttl_seconds)
        session = self._store.create_session(
            user=user,
            token=token,
            csrf_token=csrf_token,
            now=now,
            expires_at=expires_at,
        )
        return SessionView(
            user=user,
            session_id=session.session_id,
            expires_at=session.expires_at,
            csrf_token=csrf_token,
        ), token

    def register(self, request: RegisterRequest, *, client_address: str) -> tuple[SessionView, str]:
        self._require_enabled()
        now = self._clock()
        password = request.password.get_secret_value()
        user = self._store.create_user(
            account=request.account,
            display_name=request.display_name,
            password_hash=self._passwords.hash(password),
            password_policy_version=self._passwords.algorithm_version,
            now=now,
        )
        return self._new_session(user)

    def login(self, request: LoginRequest, *, client_address: str) -> tuple[SessionView, str]:
        self._require_enabled()
        now = self._clock()
        rate_key = self._rate_key(request.account, client_address)
        if self._store.rate_limit_state(rate_key, now) is not None:
            raise AuthServiceError("auth_rate_limited")
        credential = self._store.credential_for_account(request.account)
        user = credential[0] if credential is not None else None
        stored_hash = credential[1] if credential is not None else None
        valid = self._passwords.verify(stored_hash, request.password.get_secret_value())
        if not valid or user is None or user.state != "active":
            blocked = self._store.record_login_failure(
                key_hash=rate_key,
                now=now,
                window_seconds=self._settings.failed_login_window_seconds,
                limit=self._settings.failed_login_limit,
                block_seconds=self._settings.login_block_seconds,
            )
            raise AuthServiceError("auth_rate_limited" if blocked else "auth_invalid_credentials")
        self._store.clear_login_failures(rate_key)
        self._store.record_login_success(user_id=user.user_id, now=now)
        return self._new_session(user)

    def current(self, token: str | None) -> SessionView:
        self._require_enabled()
        if not token:
            raise AuthServiceError("auth_required")
        now = self._clock()
        resolved = self._store.resolve_session(token=token, now=now)
        if resolved is None:
            raise AuthServiceError("auth_required")
        user, session, _ = resolved
        csrf_token = secrets.token_urlsafe(32)
        self._store.rotate_csrf(session_id=session.session_id, csrf_token=csrf_token, now=now)
        return SessionView(
            user=user,
            session_id=session.session_id,
            expires_at=session.expires_at,
            csrf_token=csrf_token,
        )

    def require_principal(self, token: str | None) -> tuple[AuthPrincipal, str]:
        self._require_enabled()
        if not token:
            raise AuthServiceError("auth_required")
        resolved = self._store.resolve_session(token=token, now=self._clock())
        if resolved is None:
            raise AuthServiceError("auth_required")
        user, session, csrf_hash = resolved
        return AuthPrincipal(
            user_id=user.user_id,
            session_id=session.session_id,
            account=user.account,
            display_name=user.display_name,
        ), csrf_hash

    def require_write_principal(
        self,
        token: str | None,
        csrf_token: str | None,
    ) -> AuthPrincipal:
        """Resolve a live principal and verify the current session CSRF token."""
        if not csrf_token:
            raise AuthServiceError("auth_csrf_invalid")
        principal, csrf_hash = self.require_principal(token)
        if not self._store.verify_csrf_hash(csrf_hash, csrf_token):
            raise AuthServiceError("auth_csrf_invalid")
        return principal

    def workspace_identity(self, token: str | None) -> WorkspaceIdentity:
        principal, _ = self.require_principal(token)
        return WorkspaceIdentity(principal=principal)

    def logout(self, token: str | None, csrf_token: str | None) -> None:
        self._require_enabled()
        if not token or not csrf_token:
            raise AuthServiceError("auth_csrf_invalid")
        resolved = self._store.resolve_session(token=token, now=self._clock(), touch=False)
        if resolved is None:
            raise AuthServiceError("auth_required")
        _, _, csrf_hash = resolved
        if not self._store.verify_csrf_hash(csrf_hash, csrf_token):
            raise AuthServiceError("auth_csrf_invalid")
        if not self._store.revoke_session(token=token, now=self._clock()):
            raise AuthServiceError("auth_required")
