"""Small auth models used by tasgi auth backends and policies."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class Identity:
    """Authenticated identity attached to a request."""

    subject: str
    display_name: Optional[str] = None
    roles: frozenset[str] = field(default_factory=frozenset)
    scopes: frozenset[str] = field(default_factory=frozenset)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AuthContext:
    """Authentication result attached to one request."""

    identity: Optional[Identity] = None
    scheme: Optional[str] = None
    credentials: Optional[str] = None
    backend: Optional[str] = None
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def is_authenticated(self) -> bool:
        """Return whether this auth result carries an authenticated identity."""

        return self.identity is not None

    @classmethod
    def anonymous(cls) -> "AuthContext":
        """Return an anonymous auth context."""

        return cls()
