"""Framework WebSocket abstraction exposed to tasgi handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from .types import Header

if TYPE_CHECKING:
    from .app import TasgiApp
    from .types import ASGIMessage, ASGIScope, Receive, Send


_MISSING = object()


class WebSocket:
    """Small ASGI-backed WebSocket helper for tasgi route handlers."""

    def __init__(
        self,
        app: "TasgiApp",
        scope: "ASGIScope",
        receive: "Receive",
        send: "Send",
        *,
        route_params: Optional[dict[str, str]] = None,
    ) -> None:
        self.app = app
        self.scope = scope
        self._receive = receive
        self._send = send
        self.route_params = dict(route_params or {})
        self._accepted = False
        self._closed = False

    @classmethod
    def from_scope(
        cls,
        app: "TasgiApp",
        scope: "ASGIScope",
        receive: "Receive",
        send: "Send",
        *,
        route_params: Optional[dict[str, str]] = None,
    ) -> "WebSocket":
        """Create a WebSocket wrapper from ASGI connection inputs."""

        return cls(
            app,
            scope,
            receive,
            send,
            route_params=route_params,
        )

    @property
    def accepted(self) -> bool:
        """Return whether the handshake was accepted."""

        return self._accepted

    @property
    def closed(self) -> bool:
        """Return whether the connection has been closed."""

        return self._closed

    @property
    def path(self) -> str:
        """Return the WebSocket request path."""

        return str(self.scope["path"])

    @property
    def query_string(self) -> bytes:
        """Return the raw query string bytes."""

        return bytes(self.scope.get("query_string", b""))

    @property
    def query(self) -> str:
        """Return the raw query string decoded as text."""

        return self.query_string.decode("utf-8")

    @property
    def headers(self) -> list[Header]:
        """Return the request headers."""

        return list(self.scope.get("headers", []))

    @property
    def http_version(self) -> str:
        """Return the underlying HTTP version that carried the upgrade."""

        return str(self.scope.get("http_version", "1.1"))

    def header(self, name: str, default: Optional[str] = None) -> Optional[str]:
        """Return a request header value decoded as latin-1."""

        raw_name = name.lower().encode("latin-1")
        for header_name, value in self.headers:
            if header_name == raw_name:
                return value.decode("latin-1")
        return default

    def service(self, name: str, default: Any = _MISSING) -> Any:
        """Resolve a shared app service by name."""

        if default is _MISSING:
            return self.app.require_service(name)
        return self.app.get_service(name, default)

    async def accept(
        self,
        *,
        subprotocol: Optional[str] = None,
        headers: Optional[list[Header]] = None,
    ) -> None:
        """Accept the WebSocket handshake."""

        if self._accepted:
            raise RuntimeError("WebSocket has already been accepted.")
        if self._closed:
            raise RuntimeError("WebSocket is already closed.")
        await self._send(
            {
                "type": "websocket.accept",
                "subprotocol": subprotocol,
                "headers": list(headers or []),
            }
        )
        self._accepted = True

    async def receive(self) -> "ASGIMessage":
        """Receive the next WebSocket ASGI event."""

        while True:
            message = await self._receive()
            message_type = message.get("type")
            if message_type == "websocket.connect":
                continue
            if message_type == "websocket.disconnect":
                self._closed = True
            return message

    async def receive_text(self) -> str:
        """Receive the next text message."""

        message = await self.receive()
        message_type = message.get("type")
        if message_type == "websocket.disconnect":
            raise RuntimeError("WebSocket disconnected.")
        if message_type != "websocket.receive" or "text" not in message:
            raise TypeError("Expected a text WebSocket message.")
        return str(message["text"])

    async def receive_bytes(self) -> bytes:
        """Receive the next binary message."""

        message = await self.receive()
        message_type = message.get("type")
        if message_type == "websocket.disconnect":
            raise RuntimeError("WebSocket disconnected.")
        if message_type != "websocket.receive" or "bytes" not in message:
            raise TypeError("Expected a binary WebSocket message.")
        return bytes(message["bytes"])

    async def send_text(self, data: str) -> None:
        """Send one text message."""

        self._require_open_connection()
        await self._send({"type": "websocket.send", "text": data})

    async def send_bytes(self, data: bytes) -> None:
        """Send one binary message."""

        self._require_open_connection()
        await self._send({"type": "websocket.send", "bytes": data})

    async def close(self, *, code: int = 1000, reason: str = "") -> None:
        """Close the WebSocket connection."""

        if self._closed:
            return
        await self._send({"type": "websocket.close", "code": code, "reason": reason})
        self._closed = True

    def _require_open_connection(self) -> None:
        if not self._accepted:
            raise RuntimeError("WebSocket must be accepted before sending messages.")
        if self._closed:
            raise RuntimeError("WebSocket is already closed.")
