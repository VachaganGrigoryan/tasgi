"""Base auth primitives for tasgi."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from .models import AuthContext

if TYPE_CHECKING:
    from ..request import Request


class AuthBackend:
    """Base backend that resolves credentials into an auth context."""

    name = "auth"

    def authenticate(self, request: "Request") -> Optional[AuthContext]:
        """Return an auth context or ``None`` when no credentials were found."""

        raise NotImplementedError


class AuthPolicy:
    """Base policy that authorizes one already-authenticated request."""

    def authorize(self, request: "Request", auth: AuthContext) -> Any:
        """Authorize one request or raise an auth exception."""

        raise NotImplementedError
