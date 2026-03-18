"""Built-in authorization policies for tasgi."""

from __future__ import annotations

from .base import AuthPolicy
from .exceptions import AuthenticationRequired, PermissionDenied
from .models import AuthContext


class RequireAuthenticated(AuthPolicy):
    """Require a resolved authenticated identity."""

    def authorize(self, request, auth: AuthContext) -> None:
        del request
        if not auth.is_authenticated:
            raise AuthenticationRequired("Authentication required.")


class RequireScope(AuthPolicy):
    """Require one scope on the authenticated identity."""

    def __init__(self, scope: str) -> None:
        self.scope = scope

    def authorize(self, request, auth: AuthContext) -> None:
        del request
        if not auth.is_authenticated:
            raise AuthenticationRequired("Authentication required.")
        assert auth.identity is not None
        if self.scope not in auth.identity.scopes:
            raise PermissionDenied("Missing required scope %r." % self.scope)


class RequireRole(AuthPolicy):
    """Require one role on the authenticated identity."""

    def __init__(self, role: str) -> None:
        self.role = role

    def authorize(self, request, auth: AuthContext) -> None:
        del request
        if not auth.is_authenticated:
            raise AuthenticationRequired("Authentication required.")
        assert auth.identity is not None
        if self.role not in auth.identity.roles:
            raise PermissionDenied("Missing required role %r." % self.role)
