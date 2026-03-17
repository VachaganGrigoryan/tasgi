"""Request abstraction exposed to tasgi handlers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Optional

from .types import ASGIScope, Header

if TYPE_CHECKING:
    from .app import TasgiApp


@dataclass(frozen=True)
class Request:
    """Buffered HTTP request built from the ASGI scope and body."""

    app: "TasgiApp"
    method: str
    path: str
    query_string: bytes
    headers: list[Header] = field(default_factory=list)
    body: bytes = b""
    http_version: str = "1.1"
    route_params: dict[str, str] = field(default_factory=dict)
    scope: ASGIScope = field(default_factory=dict)

    @classmethod
    def from_scope(
        cls,
        app: "TasgiApp",
        scope: ASGIScope,
        body: bytes,
        route_params: Optional[Dict[str, str]] = None,
    ) -> "Request":
        """Create a request from ASGI HTTP scope data and a buffered body."""

        return cls(
            app=app,
            method=str(scope["method"]),
            path=str(scope["path"]),
            query_string=bytes(scope.get("query_string", b"")),
            headers=list(scope.get("headers", [])),
            body=body,
            http_version=str(scope.get("http_version", "1.1")),
            route_params=dict(route_params or {}),
            scope=scope,
        )

    def text(self, encoding: str = "utf-8") -> str:
        """Decode the buffered request body as text."""

        return self.body.decode(encoding)

    @property
    def query(self) -> str:
        """Return the raw query string decoded as text."""

        return self.query_string.decode("utf-8")

    def json(self) -> Any:
        """Decode the buffered request body as JSON."""

        return json.loads(self.text())

    def header(self, name: str, default: Optional[str] = None) -> Optional[str]:
        """Return a request header value decoded as latin-1."""

        raw_name = name.lower().encode("latin-1")
        for header_name, value in self.headers:
            if header_name == raw_name:
                return value.decode("latin-1")
        return default
