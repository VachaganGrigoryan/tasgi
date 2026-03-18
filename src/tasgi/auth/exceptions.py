"""Auth-specific exceptions raised by tasgi auth policies."""

from __future__ import annotations


class AuthError(Exception):
    """Base class for tasgi auth errors."""


class AuthenticationRequired(AuthError):
    """Raised when a protected route has no authenticated identity."""


class AuthenticationFailed(AuthError):
    """Raised when presented credentials are invalid."""


class PermissionDenied(AuthError):
    """Raised when an authenticated identity lacks permission."""
