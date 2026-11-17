"""Default-off product project authorization boundary for PV2-03b."""

from .models import (
    AuthorizationFailure,
    AuthorizedResource,
    ProjectAuthorizationSettings,
    ProjectOwner,
    ProjectTaskBinding,
)
from .service import ProjectAuthorizationService, project_scope_key
from .settings import load_project_authorization_settings

__all__ = [
    "AuthorizationFailure",
    "AuthorizedResource",
    "ProjectAuthorizationService",
    "ProjectAuthorizationSettings",
    "ProjectOwner",
    "ProjectTaskBinding",
    "load_project_authorization_settings",
    "project_scope_key",
]
