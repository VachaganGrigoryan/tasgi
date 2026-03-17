"""Minimal HTTP/2 helpers for tasgi's transport layer."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

from .types import Header, RequestData

CLIENT_CONNECTION_PREFACE = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"

FRAME_DATA = 0x0
FRAME_HEADERS = 0x1
FRAME_SETTINGS = 0x4
FRAME_WINDOW_UPDATE = 0x8

FLAG_ACK = 0x1
FLAG_END_STREAM = 0x1
FLAG_END_HEADERS = 0x4


class HTTP2ProtocolError(RuntimeError):
    """Raised when a peer sends unsupported or invalid HTTP/2 input."""


@dataclass(frozen=True)
class HTTP2Frame:
    """One parsed HTTP/2 frame."""

    frame_type: int
    flags: int
    stream_id: int
    payload: bytes


async def read_frame(reader: asyncio.StreamReader) -> HTTP2Frame:
    """Read one HTTP/2 frame from a byte stream."""

    header = await reader.readexactly(9)
    length = int.from_bytes(header[:3], "big")
    frame_type = header[3]
    flags = header[4]
    stream_id = int.from_bytes(header[5:9], "big") & 0x7FFF_FFFF
    payload = await reader.readexactly(length)
    return HTTP2Frame(
        frame_type=frame_type,
        flags=flags,
        stream_id=stream_id,
        payload=payload,
    )


def encode_frame(frame_type: int, flags: int, stream_id: int, payload: bytes = b"") -> bytes:
    """Serialize one HTTP/2 frame."""

    if stream_id < 0:
        raise ValueError("HTTP/2 stream ids must be non-negative.")
    length = len(payload)
    if length >= 2**24:
        raise ValueError("HTTP/2 frame payload too large for this prototype.")
    return (
        length.to_bytes(3, "big")
        + bytes([frame_type, flags])
        + (stream_id & 0x7FFF_FFFF).to_bytes(4, "big")
        + payload
    )


def encode_settings_frame(*, ack: bool = False) -> bytes:
    """Serialize an empty SETTINGS frame."""

    return encode_frame(FRAME_SETTINGS, FLAG_ACK if ack else 0, 0, b"")


def encode_headers_frame(
    stream_id: int,
    headers: list[Header],
    *,
    end_stream: bool,
) -> bytes:
    """Serialize one HEADERS frame with a minimal HPACK block."""

    flags = FLAG_END_HEADERS
    if end_stream:
        flags |= FLAG_END_STREAM
    return encode_frame(
        FRAME_HEADERS,
        flags,
        stream_id,
        encode_header_block(headers),
    )


def encode_data_frame(stream_id: int, body: bytes, *, end_stream: bool) -> bytes:
    """Serialize one DATA frame."""

    flags = FLAG_END_STREAM if end_stream else 0
    return encode_frame(FRAME_DATA, flags, stream_id, body)


def encode_header_block(headers: list[Header]) -> bytes:
    """Encode a tiny HPACK subset using literal headers without indexing."""

    block = bytearray()
    for name, value in headers:
        if not isinstance(name, bytes) or not isinstance(value, bytes):
            raise TypeError("HTTP/2 header names and values must be bytes.")
        block.append(0x00)
        block.extend(_encode_string(name))
        block.extend(_encode_string(value))
    return bytes(block)


def decode_header_block(block: bytes) -> list[Header]:
    """Decode the tiny HPACK subset used by the tasgi prototype."""

    headers: list[Header] = []
    index = 0
    while index < len(block):
        first = block[index]
        if (first & 0xF0) not in {0x00, 0x10}:
            raise HTTP2ProtocolError("Unsupported HPACK representation in prototype.")
        if (first & 0x0F) != 0:
            raise HTTP2ProtocolError("Indexed HPACK names are not supported in prototype.")
        index += 1
        name, index = _decode_string(block, index)
        value, index = _decode_string(block, index)
        headers.append((name, value))
    return headers


def request_data_from_headers(headers: list[Header], body: bytes) -> RequestData:
    """Build buffered request data from HTTP/2 pseudo-headers and body bytes."""

    pseudo_headers: dict[bytes, bytes] = {}
    regular_headers: list[Header] = []

    for name, value in headers:
        if name.startswith(b":"):
            pseudo_headers[name] = value
        else:
            regular_headers.append((name.lower(), value))

    method = _require_pseudo_header(pseudo_headers, b":method").decode("ascii")
    raw_path = _require_pseudo_header(pseudo_headers, b":path")
    scheme = pseudo_headers.get(b":scheme", b"http").decode("ascii")
    authority = pseudo_headers.get(b":authority")

    if authority is not None and not any(name == b"host" for name, _ in regular_headers):
        regular_headers.append((b"host", authority))

    path_bytes, _, query_string = raw_path.partition(b"?")
    path = path_bytes.decode("ascii") if path_bytes else "/"
    if not path.startswith("/"):
        raise HTTP2ProtocolError("HTTP/2 :path must be absolute.")

    return RequestData(
        method=method,
        path=path,
        query_string=query_string,
        headers=regular_headers,
        body=body,
        http_version="2",
        scheme=scheme,
    )


def _require_pseudo_header(headers: dict[bytes, bytes], name: bytes) -> bytes:
    if name not in headers:
        raise HTTP2ProtocolError("Missing required HTTP/2 pseudo-header %s." % name.decode("ascii"))
    return headers[name]


def _encode_string(value: bytes) -> bytes:
    length = len(value)
    if length >= 127:
        raise HTTP2ProtocolError("Prototype HPACK strings must be shorter than 127 bytes.")
    return bytes([length]) + value


def _decode_string(block: bytes, index: int) -> tuple[bytes, int]:
    if index >= len(block):
        raise HTTP2ProtocolError("Unexpected end of HPACK string.")
    length_byte = block[index]
    if length_byte & 0x80:
        raise HTTP2ProtocolError("Huffman-coded HPACK strings are not supported in prototype.")
    length = length_byte & 0x7F
    start = index + 1
    end = start + length
    if end > len(block):
        raise HTTP2ProtocolError("Incomplete HPACK string.")
    return block[start:end], end
