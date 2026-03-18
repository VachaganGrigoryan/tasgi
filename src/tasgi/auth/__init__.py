"""Public auth API for tasgi."""

from .backends import APIKeyBackend, BearerTokenBackend
from .base import AuthBackend, AuthPolicy
from .exceptions import AuthError, AuthenticationFailed, AuthenticationRequired, PermissionDenied
from .models import AuthContext, Identity
from .policies import RequireAuthenticated, RequireRole, RequireScope

__all__ = [
    "APIKeyBackend",
    "AuthBackend",
    "AuthContext",
    "AuthError",
    "AuthPolicy",
    "AuthenticationFailed",
    "AuthenticationRequired",
    "BearerTokenBackend",
    "Identity",
    "PermissionDenied",
    "RequireAuthenticated",
    "RequireRole",
    "RequireScope",
]
