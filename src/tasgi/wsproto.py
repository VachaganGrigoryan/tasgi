"""Minimal RFC6455 helpers for tasgi's WebSocket transport."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import struct
from dataclasses import dataclass
from typing import Optional

from .types import Header, RequestData

WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OPCODE_CONTINUATION = 0x0
OPCODE_TEXT = 0x1
OPCODE_BINARY = 0x2
OPCODE_CLOSE = 0x8
OPCODE_PING = 0x9
OPCODE_PONG = 0xA


class WebSocketProtocolError(RuntimeError):
    """Raised when a WebSocket peer violates the supported protocol subset."""


@dataclass(frozen=True)
class WebSocketFrame:
    """One parsed WebSocket frame."""

    opcode: int
    payload: bytes
    fin: bool = True


def is_websocket_upgrade(request: RequestData) -> bool:
    """Return whether an HTTP request asks to upgrade to WebSocket."""

    if request.method.upper() != "GET":
        return False

    upgrade = _get_header(request.headers, b"upgrade")
    connection = _get_header(request.headers, b"connection")
    version = _get_header(request.headers, b"sec-websocket-version")
    key = _get_header(request.headers, b"sec-websocket-key")
    if upgrade is None or connection is None or version is None or key is None:
        return False

    return upgrade.lower() == b"websocket" and b"upgrade" in connection.lower() and version == b"13"


def build_accept_token(websocket_key: str) -> str:
    """Build the Sec-WebSocket-Accept header value."""

    digest = hashlib.sha1((websocket_key + WEBSOCKET_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def build_handshake_response(
    accept_token: str,
    *,
    subprotocol: Optional[str] = None,
    headers: Optional[list[Header]] = None,
) -> bytes:
    """Build a successful HTTP/1.1 WebSocket upgrade response."""

    response_headers = [
        b"HTTP/1.1 101 Switching Protocols",
        b"Upgrade: websocket",
        b"Connection: Upgrade",
        b"Sec-WebSocket-Accept: " + accept_token.encode("ascii"),
    ]
    if subprotocol is not None:
        response_headers.append(b"Sec-WebSocket-Protocol: " + subprotocol.encode("ascii"))
    for name, value in list(headers or []):
        response_headers.append(name + b": " + value)
    return b"\r\n".join(response_headers) + b"\r\n\r\n"


def build_rejection_response(status: int = 403, body: bytes = b"WebSocket connection rejected.") -> bytes:
    """Build a plain HTTP rejection response before the handshake is accepted."""

    reason = "Forbidden" if status == 403 else "Bad Request"
    return (
        f"HTTP/1.1 {status} {reason}\r\n".encode("ascii")
        + b"content-type: text/plain; charset=utf-8\r\n"
        + b"content-length: "
        + str(len(body)).encode("ascii")
        + b"\r\n\r\n"
        + body
    )


async def read_frame(reader: asyncio.StreamReader) -> WebSocketFrame:
    """Read one WebSocket frame from the client."""

    header = await reader.readexactly(2)
    first, second = header
    fin = bool(first & 0x80)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    payload_length = second & 0x7F

    if not fin:
        raise WebSocketProtocolError("Fragmented WebSocket frames are not supported in prototype.")
    if not masked:
        raise WebSocketProtocolError("Client WebSocket frames must be masked.")

    if payload_length == 126:
        payload_length = int.from_bytes(await reader.readexactly(2), "big")
    elif payload_length == 127:
        payload_length = int.from_bytes(await reader.readexactly(8), "big")

    mask_key = await reader.readexactly(4)
    payload = bytearray(await reader.readexactly(payload_length))
    for index in range(payload_length):
        payload[index] ^= mask_key[index % 4]

    return WebSocketFrame(opcode=opcode, payload=bytes(payload), fin=fin)


def encode_frame(opcode: int, payload: bytes = b"", *, fin: bool = True) -> bytes:
    """Encode one server-to-client WebSocket frame."""

    first = opcode | (0x80 if fin else 0)
    length = len(payload)
    if length < 126:
        header = bytes([first, length])
    elif length < 2**16:
        header = bytes([first, 126]) + length.to_bytes(2, "big")
    else:
        header = bytes([first, 127]) + length.to_bytes(8, "big")
    return header + payload


def encode_close_payload(code: int, reason: str = "") -> bytes:
    """Encode a close payload body."""

    if code < 1000:
        raise ValueError("WebSocket close code must be >= 1000.")
    reason_bytes = reason.encode("utf-8")
    return struct.pack("!H", code) + reason_bytes


def decode_close_payload(payload: bytes) -> tuple[int, str]:
    """Decode a close payload body."""

    if not payload:
        return 1005, ""
    if len(payload) == 1:
        raise WebSocketProtocolError("Invalid WebSocket close payload.")
    code = struct.unpack("!H", payload[:2])[0]
    reason = payload[2:].decode("utf-8")
    return code, reason


def _get_header(headers: list[Header], name: bytes) -> Optional[bytes]:
    lowered_name = name.lower()
    for header_name, value in headers:
        if header_name == lowered_name:
            return value
    return None
