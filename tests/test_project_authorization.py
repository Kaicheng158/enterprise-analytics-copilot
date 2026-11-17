"""Offline contract and integration-boundary tests for PV2-03b."""

from pathlib import Path
import unittest
import uuid

from pydantic import ValidationError

from backend.auth.models import AuthPrincipal
from backend.authorization.models import ProjectAuthorizationSettings
from backend.authorization.service import project_scope_key
from backend.authorization.settings import load_project_authorization_settings


ROOT = Path(__file__).resolve().parents[1]


class ProjectAuthorizationOfflineTests(unittest.TestCase):
    def test_feature_defaults_off_and_scope_is_server_derived(self):
        self.assertFalse(ProjectAuthorizationSettings().enabled)
        project_id = uuid.uuid4()
        self.assertEqual(project_scope_key(project_id), f"product-project:{project_id}")
        self.assertIn("PROJECT_AUTHORIZATION_ENABLED=false", (ROOT / ".env.example").read_text())

    def test_environment_flag_is_strict_and_defaults_off(self):
        from unittest.mock import patch

        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(load_project_authorization_settings().enabled)
        with patch.dict("os.environ", {"PROJECT_AUTHORIZATION_ENABLED": "not-a-bool"}, clear=True):
            with self.assertRaisesRegex(ValueError, "Invalid PROJECT_AUTHORIZATION_ENABLED"):
                load_project_authorization_settings()

    def test_browser_cannot_construct_principal_from_demo_or_extra_claims(self):
        with self.assertRaises(ValidationError):
            AuthPrincipal.model_validate(
                {
                    "user_id": str(uuid.uuid4()),
                    "session_id": str(uuid.uuid4()),
                    "account": "demo@example.com",
                    "display_name": "Demo",
                    "role": "owner",
                },
                strict=True,
            )

    def test_existing_apps_do_not_publish_project_authorization_routes(self):
        from backend.main import app as chat_app
        from backend.local_auth_app import app as auth_app

        paths = {
            path
            for app in (chat_app, auth_app)
            for route in app.routes
            if (path := getattr(route, "path", None)) is not None
        }
        self.assertFalse(any("project" in path or "asset" in path for path in paths))


if __name__ == "__main__":
    unittest.main()
