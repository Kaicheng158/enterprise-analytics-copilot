"""Offline contracts and default-off safety checks for PV2-03a."""

from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from backend.auth.models import LoginRequest, RegisterRequest, normalize_account
from backend.auth.passwords import Argon2PasswordService
from backend.auth.settings import LocalAuthSettings, load_local_auth_settings
from backend.main import app as main_app
from backend.workspace_bridge_app import app as bridge_app


class LocalAuthOfflineTests(unittest.TestCase):
    def test_feature_flag_defaults_off_and_requires_server_secret_when_enabled(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(load_local_auth_settings().enabled)
        with self.assertRaises(ValidationError):
            LocalAuthSettings(enabled=True)
        enabled = LocalAuthSettings(enabled=True, rate_limit_secret="s" * 32)
        self.assertTrue(enabled.enabled)

    def test_production_cookie_contract_requires_https_and_secure(self):
        with self.assertRaises(ValidationError):
            LocalAuthSettings(
                deployment_mode="production",
                allowed_origin="http://example.com",
                cookie_secure=False,
            )
        production = LocalAuthSettings(
            deployment_mode="production",
            allowed_origin="https://analytics.example.com",
            cookie_secure=True,
        )
        self.assertTrue(production.cookie_secure)
        self.assertEqual(production.cookie_same_site, "strict")

    def test_account_and_requests_are_strict_and_deterministic(self):
        self.assertEqual(normalize_account("  Analyst@Example.COM "), "analyst@example.com")
        request = RegisterRequest(
            account="Analyst@Example.COM",
            display_name="  Synthetic   Analyst ",
            password="synthetic-password-123",
        )
        self.assertEqual(request.account, "analyst@example.com")
        self.assertEqual(request.display_name, "Synthetic Analyst")
        self.assertNotIn("synthetic-password", repr(request))
        with self.assertRaises(ValidationError):
            LoginRequest(account="not-an-email", password="anything")
        with self.assertRaises(ValidationError):
            RegisterRequest(
                account="a@example.com",
                display_name="A",
                password="too-short",
                client_role="owner",
            )

    def test_argon2id_hash_is_salted_and_plaintext_is_never_embedded(self):
        passwords = Argon2PasswordService(time_cost=1, memory_cost=8_192, parallelism=1)
        first = passwords.hash("synthetic-password-123")
        second = passwords.hash("synthetic-password-123")
        self.assertTrue(first.startswith("$argon2id$"))
        self.assertNotEqual(first, second)
        self.assertNotIn("synthetic-password", first)
        self.assertTrue(passwords.verify(first, "synthetic-password-123"))
        self.assertFalse(passwords.verify(first, "wrong-password"))
        self.assertFalse(passwords.verify(None, "synthetic-password-123"))

    def test_auth_surface_is_not_mounted_on_existing_apps(self):
        for app in (main_app, bridge_app):
            paths = {route.path for route in app.routes if hasattr(route, "path")}
            self.assertFalse(any(path.startswith("/auth-api/") for path in paths))

    def test_frontend_keeps_demo_and_real_auth_modes_separate(self):
        source = (Path(__file__).parents[1] / "frontend/workspace/app.js").read_text()
        client = (Path(__file__).parents[1] / "frontend/workspace/auth-client.js").read_text()
        self.assertIn("if (!realAuth) { go('overview'); return; }", source)
        self.assertIn("/auth-api/config", client)
        self.assertNotIn("localStorage.setItem", client)
        self.assertNotIn("sessionStorage.setItem", client)


if __name__ == "__main__":
    unittest.main()
