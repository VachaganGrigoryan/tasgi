"""ASGI boundary helpers."""

from __future__ import annotations

from .exceptions import HTTPError
from .request import Request
from .response import Response
from .types import ASGIMessage, ASGIScope, Send


def validate_http_scope(scope: ASGIScope) -> None:
    """Ensure the ASGI scope is an HTTP connection."""

    if scope.get("type") != "http":
        raise ValueError("tasgi only supports HTTP scopes in this prototype.")


async def receive_request_body(receive, max_request_body_size: int) -> bytes:
    """Collect and size-check the fully buffered HTTP request body."""

    body_parts: list[bytes] = []
    total_size = 0

    while True:
        message: ASGIMessage = await receive()
        message_type = message.get("type")
        if message_type == "http.disconnect":
            break
        if message_type != "http.request":
            raise ValueError(f"Unsupported inbound ASGI message type: {message_type!r}")
        body = message.get("body", b"")
        if not isinstance(body, (bytes, bytearray)):
            raise TypeError("Request body must be bytes.")

        body_bytes = bytes(body)
        total_size += len(body_bytes)
        if total_size > max_request_body_size:
            raise HTTPError(413, "Request body too large")

        body_parts.append(body_bytes)
        if not message.get("more_body", False):
            break

    return b"".join(body_parts)


async def send_response(send: Send, response: Response) -> None:
    """Emit a complete ASGI HTTP response."""

    async for message in response.iter_asgi_messages():
        await send(message)


def build_request(app, scope: ASGIScope, body: bytes, route_params=None) -> Request:
    """Create a request object from ASGI inputs."""

    return Request.from_scope(app, scope, body, route_params=route_params)
