"""Core typing primitives used by the prototype."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

Header = tuple[bytes, bytes]
ASGIScope = dict[str, Any]
ASGIMessage = dict[str, Any]
Receive = Callable[[], Awaitable[ASGIMessage]]
Send = Callable[[ASGIMessage], Awaitable[None]]


class ASGIApp(Protocol):
    """Protocol for a minimal async ASGI application."""

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        """Handle a single ASGI connection."""


@dataclass(frozen=True)
class RequestData:
    """Buffered HTTP request data used by the parser and sync adapter."""

    method: str
    path: str
    query_string: bytes
    headers: list[Header] = field(default_factory=list)
    body: bytes = b""
    http_version: str = "1.1"
    scheme: str = "http"


@dataclass(frozen=True)
class ResponseData:
    """Buffered HTTP response returned by sync handlers."""

    status: int
    headers: list[Header] = field(default_factory=list)
    body: bytes = b""


class SyncHandler(Protocol):
    """Protocol for the sync request handler used by worker threads."""

    def __call__(self, request: RequestData) -> ResponseData:
        """Compute a response from a fully buffered request."""
