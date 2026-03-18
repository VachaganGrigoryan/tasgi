"""Minimal HTTP/2 helpers and state managers for tasgi's transport layer."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
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

HPACK_STATIC_TABLE: tuple[Header, ...] = (
    (b":authority", b""),
    (b":method", b"GET"),
    (b":method", b"POST"),
    (b":path", b"/"),
    (b":path", b"/index.html"),
    (b":scheme", b"http"),
    (b":scheme", b"https"),
    (b":status", b"200"),
    (b":status", b"204"),
    (b":status", b"206"),
    (b":status", b"304"),
    (b":status", b"400"),
    (b":status", b"404"),
    (b":status", b"500"),
    (b"accept-charset", b""),
    (b"accept-encoding", b"gzip, deflate"),
    (b"accept-language", b""),
    (b"accept-ranges", b""),
    (b"accept", b""),
    (b"access-control-allow-origin", b""),
    (b"age", b""),
    (b"allow", b""),
    (b"authorization", b""),
    (b"cache-control", b""),
    (b"content-disposition", b""),
    (b"content-encoding", b""),
    (b"content-language", b""),
    (b"content-length", b""),
    (b"content-location", b""),
    (b"content-range", b""),
    (b"content-type", b""),
    (b"cookie", b""),
    (b"date", b""),
    (b"etag", b""),
    (b"expect", b""),
    (b"expires", b""),
    (b"from", b""),
    (b"host", b""),
    (b"if-match", b""),
    (b"if-modified-since", b""),
    (b"if-none-match", b""),
    (b"if-range", b""),
    (b"if-unmodified-since", b""),
    (b"last-modified", b""),
    (b"link", b""),
    (b"location", b""),
    (b"max-forwards", b""),
    (b"proxy-authenticate", b""),
    (b"proxy-authorization", b""),
    (b"range", b""),
    (b"referer", b""),
    (b"refresh", b""),
    (b"retry-after", b""),
    (b"server", b""),
    (b"set-cookie", b""),
    (b"strict-transport-security", b""),
    (b"transfer-encoding", b""),
    (b"user-agent", b""),
    (b"vary", b""),
    (b"via", b""),
    (b"www-authenticate", b""),
)

# Printable ASCII symbols plus EOS, which is sufficient for local curl/demo requests.
HPACK_HUFFMAN_CODES: tuple[tuple[int, int, int], ...] = (
    (32, 0x14, 6),
    (33, 0x3F8, 10),
    (34, 0x3F9, 10),
    (35, 0xFFA, 12),
    (36, 0x1FF9, 13),
    (37, 0x15, 6),
    (38, 0xF8, 8),
    (39, 0x7FA, 11),
    (40, 0x3FA, 10),
    (41, 0x3FB, 10),
    (42, 0xF9, 8),
    (43, 0x7FB, 11),
    (44, 0xFA, 8),
    (45, 0x16, 6),
    (46, 0x17, 6),
    (47, 0x18, 6),
    (48, 0x0, 5),
    (49, 0x1, 5),
    (50, 0x2, 5),
    (51, 0x19, 6),
    (52, 0x1A, 6),
    (53, 0x1B, 6),
    (54, 0x1C, 6),
    (55, 0x1D, 6),
    (56, 0x1E, 6),
    (57, 0x1F, 6),
    (58, 0x5C, 7),
    (59, 0xFB, 8),
    (60, 0x7FFC, 15),
    (61, 0x20, 6),
    (62, 0xFFB, 12),
    (63, 0x3FC, 10),
    (64, 0x1FFA, 13),
    (65, 0x21, 6),
    (66, 0x5D, 7),
    (67, 0x5E, 7),
    (68, 0x5F, 7),
    (69, 0x60, 7),
    (70, 0x61, 7),
    (71, 0x62, 7),
    (72, 0x63, 7),
    (73, 0x64, 7),
    (74, 0x65, 7),
    (75, 0x66, 7),
    (76, 0x67, 7),
    (77, 0x68, 7),
    (78, 0x69, 7),
    (79, 0x6A, 7),
    (80, 0x6B, 7),
    (81, 0x6C, 7),
    (82, 0x6D, 7),
    (83, 0x6E, 7),
    (84, 0x6F, 7),
    (85, 0x70, 7),
    (86, 0x71, 7),
    (87, 0x72, 7),
    (88, 0xFC, 8),
    (89, 0x73, 7),
    (90, 0xFD, 8),
    (91, 0x1FFB, 13),
    (92, 0x7FFF0, 19),
    (93, 0x1FFC, 13),
    (94, 0x3FFC, 14),
    (95, 0x22, 6),
    (96, 0x7FFD, 15),
    (97, 0x3, 5),
    (98, 0x23, 6),
    (99, 0x4, 5),
    (100, 0x24, 6),
    (101, 0x5, 5),
    (102, 0x25, 6),
    (103, 0x26, 6),
    (104, 0x27, 6),
    (105, 0x6, 5),
    (106, 0x74, 7),
    (107, 0x75, 7),
    (108, 0x28, 6),
    (109, 0x29, 6),
    (110, 0x2A, 6),
    (111, 0x7, 5),
    (112, 0x2B, 6),
    (113, 0x76, 7),
    (114, 0x2C, 6),
    (115, 0x8, 5),
    (116, 0x9, 5),
    (117, 0x2D, 6),
    (118, 0x77, 7),
    (119, 0x78, 7),
    (120, 0x79, 7),
    (121, 0x7A, 7),
    (122, 0x7B, 7),
    (123, 0x7FFE, 15),
    (124, 0x7FC, 11),
    (125, 0x3FFD, 14),
    (126, 0x1FFD, 13),
    (256, 0x3FFFFFFF, 30),
)

HPACK_HUFFMAN_TREE: dict[int, object]


class HTTP2ProtocolError(RuntimeError):
    """Raised when a peer sends unsupported or invalid HTTP/2 input."""


@dataclass(frozen=True)
class HTTP2Frame:
    """One parsed HTTP/2 frame."""

    frame_type: int
    flags: int
    stream_id: int
    payload: bytes


@dataclass
class HTTP2Stream:
    """Per-stream HTTP/2 request state."""

    stream_id: int
    headers: list[Header] = field(default_factory=list)
    body: bytearray = field(default_factory=bytearray)
    received_headers: bool = False
    request_complete: bool = False

    def receive_headers(self, frame: HTTP2Frame) -> bool:
        """Consume a HEADERS frame and report whether the request is complete."""

        if self.received_headers:
            raise HTTP2ProtocolError("Repeated HEADERS frames are not supported in prototype.")
        if not (frame.flags & FLAG_END_HEADERS):
            raise HTTP2ProtocolError("CONTINUATION frames are not supported in prototype.")

        self.headers = decode_header_block(frame.payload)
        self.received_headers = True
        if frame.flags & FLAG_END_STREAM:
            self.request_complete = True
        return self.request_complete

    def receive_data(self, frame: HTTP2Frame) -> bool:
        """Consume a DATA frame and report whether the request is complete."""

        if not self.received_headers:
            raise HTTP2ProtocolError("Received DATA for an HTTP/2 stream before HEADERS.")
        self.body.extend(frame.payload)
        if frame.flags & FLAG_END_STREAM:
            self.request_complete = True
        return self.request_complete

    def to_request_data(self) -> RequestData:
        """Convert the buffered stream into one request object."""

        if not self.request_complete:
            raise HTTP2ProtocolError("HTTP/2 stream is not complete yet.")
        return request_data_from_headers(self.headers, bytes(self.body))


@dataclass
class HTTP2Connection:
    """Connection-scoped HTTP/2 transport state for the demo server."""

    streams: dict[int, HTTP2Stream] = field(default_factory=dict)
    settings_received: bool = False

    def validate_client_preface(self, preface: bytes) -> None:
        """Ensure the client sent the HTTP/2 connection preface."""

        if preface != CLIENT_CONNECTION_PREFACE:
            raise HTTP2ProtocolError("Missing HTTP/2 client connection preface.")

    def handle_frame(self, frame: HTTP2Frame) -> Optional[HTTP2Stream]:
        """Route one frame to connection or stream state."""

        if frame.frame_type == FRAME_SETTINGS:
            self._handle_settings(frame)
            return None

        if frame.frame_type == FRAME_WINDOW_UPDATE:
            return None

        if frame.stream_id == 0:
            raise HTTP2ProtocolError("HTTP/2 request frames must use a non-zero stream id.")

        stream = self.streams.setdefault(frame.stream_id, HTTP2Stream(stream_id=frame.stream_id))
        if frame.frame_type == FRAME_HEADERS:
            complete = stream.receive_headers(frame)
        elif frame.frame_type == FRAME_DATA:
            complete = stream.receive_data(frame)
        else:
            raise HTTP2ProtocolError(
                "Unsupported HTTP/2 frame type %d in prototype." % frame.frame_type
            )

        if complete:
            return self.streams.pop(frame.stream_id)
        return None

    def _handle_settings(self, frame: HTTP2Frame) -> None:
        if frame.stream_id != 0:
            raise HTTP2ProtocolError("HTTP/2 SETTINGS frames must use stream 0.")
        if frame.flags & FLAG_ACK:
            return
        self.settings_received = True


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
    """Decode the HPACK subset needed by the tasgi prototype."""

    headers: list[Header] = []
    index = 0
    while index < len(block):
        first = block[index]
        if first & 0x80:
            header_index, index = _decode_integer(block, index, 7)
            headers.append(_lookup_static_header(header_index))
            continue
        if first & 0x40:
            header, index = _decode_literal_header(block, index, 6)
            headers.append(header)
            continue
        if first & 0x20:
            _table_size, index = _decode_integer(block, index, 5)
            continue
        if (first & 0xF0) in {0x00, 0x10}:
            header, index = _decode_literal_header(block, index, 4)
            headers.append(header)
            continue
        raise HTTP2ProtocolError("Unsupported HPACK representation in prototype.")
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


def _decode_integer(block: bytes, index: int, prefix_bits: int) -> tuple[int, int]:
    if index >= len(block):
        raise HTTP2ProtocolError("Unexpected end of HPACK integer.")

    mask = (1 << prefix_bits) - 1
    first = block[index]
    value = first & mask
    index += 1
    if value < mask:
        return value, index

    shift = 0
    while True:
        if index >= len(block):
            raise HTTP2ProtocolError("Incomplete HPACK integer.")
        byte = block[index]
        index += 1
        value += (byte & 0x7F) << shift
        if not (byte & 0x80):
            return value, index
        shift += 7


def _decode_string(block: bytes, index: int) -> tuple[bytes, int]:
    if index >= len(block):
        raise HTTP2ProtocolError("Unexpected end of HPACK string.")
    length_byte = block[index]
    huffman = bool(length_byte & 0x80)
    length, start = _decode_integer(block, index, 7)
    end = start + length
    if end > len(block):
        raise HTTP2ProtocolError("Incomplete HPACK string.")
    value = block[start:end]
    if huffman:
        return _decode_huffman_string(value), end
    return value, end


def _decode_literal_header(block: bytes, index: int, prefix_bits: int) -> tuple[Header, int]:
    name_index, index = _decode_integer(block, index, prefix_bits)
    if name_index == 0:
        name, index = _decode_string(block, index)
    else:
        name = _lookup_static_header(name_index)[0]
    value, index = _decode_string(block, index)
    return (name, value), index


def _lookup_static_header(index: int) -> Header:
    if index <= 0:
        raise HTTP2ProtocolError("HPACK header index must be positive.")
    if index > len(HPACK_STATIC_TABLE):
        raise HTTP2ProtocolError("Dynamic HPACK table is not supported in prototype.")
    return HPACK_STATIC_TABLE[index - 1]


def _decode_huffman_string(data: bytes) -> bytes:
    root = HPACK_HUFFMAN_TREE
    node = root
    output = bytearray()
    trailing_bits = 0
    trailing_value = 0

    for byte in data:
        for shift in range(7, -1, -1):
            bit = (byte >> shift) & 1
            trailing_bits += 1
            trailing_value = ((trailing_value << 1) | bit) & 0x7F
            if bit not in node:
                raise HTTP2ProtocolError("Unsupported HPACK Huffman code in prototype.")
            node = node[bit]
            if isinstance(node, int):
                if node == 256:
                    raise HTTP2ProtocolError("Unexpected HPACK EOS symbol in string literal.")
                output.append(node)
                node = root
                trailing_bits = 0
                trailing_value = 0

    if node is not root:
        if trailing_bits > 7:
            raise HTTP2ProtocolError("Invalid HPACK Huffman padding.")
        if trailing_value != (1 << trailing_bits) - 1:
            raise HTTP2ProtocolError("Invalid HPACK Huffman padding.")

    return bytes(output)


def _build_huffman_tree() -> dict[int, object]:
    tree: dict[int, object] = {}
    for symbol, code, bit_length in HPACK_HUFFMAN_CODES:
        node = tree
        for shift in range(bit_length - 1, -1, -1):
            bit = (code >> shift) & 1
            if shift == 0:
                node[bit] = symbol
            else:
                child = node.get(bit)
                if child is None:
                    child = {}
                    node[bit] = child
                if isinstance(child, int):
                    raise RuntimeError("Invalid HPACK Huffman tree construction.")
                node = child
    return tree


HPACK_HUFFMAN_TREE = _build_huffman_tree()
