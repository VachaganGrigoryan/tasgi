"""Tiny HTTP parsing helpers for the teaching prototype."""

from __future__ import annotations

from dataclasses import dataclass

from .types import Header, RequestData

_HEADER_TERMINATOR = b"\r\n\r\n"
_SUPPORTED_METHODS = {"GET", "POST"}


class HTTPParseError(ValueError):
    """Raised when the prototype parser receives malformed HTTP input."""


@dataclass(frozen=True)
class RequestHead:
    """Parsed request-line and header metadata."""

    method: str
    path: str
    query_string: bytes
    headers: list[Header]
    http_version: str
    content_length: int


def parse_request_head(raw_head: bytes) -> RequestHead:
    """Parse a request line and headers from a buffered HTTP head block."""

    if not raw_head.endswith(_HEADER_TERMINATOR):
        raise HTTPParseError("HTTP head must end with CRLF CRLF.")

    lines = raw_head[:-4].split(b"\r\n")
    if not lines or not lines[0]:
        raise HTTPParseError("HTTP request line is missing.")

    request_line = lines[0].decode("ascii", errors="strict")
    parts = request_line.split(" ")
    if len(parts) != 3:
        raise HTTPParseError("Malformed HTTP request line.")

    method, target, version = parts
    if method not in _SUPPORTED_METHODS:
        raise HTTPParseError(f"Unsupported HTTP method: {method}.")
    if not version.startswith("HTTP/"):
        raise HTTPParseError("Malformed HTTP version.")
    if not target.startswith("/"):
        raise HTTPParseError("Only origin-form request targets are supported.")

    raw_path, _, raw_query = target.encode("ascii", errors="strict").partition(b"?")
    headers = _parse_headers(lines[1:])
    content_length = _extract_content_length(headers)

    return RequestHead(
        method=method,
        path=raw_path.decode("ascii", errors="strict"),
        query_string=raw_query,
        headers=headers,
        http_version=version.removeprefix("HTTP/"),
        content_length=content_length,
    )


def parse_http_request(raw_request: bytes) -> RequestData:
    """Parse a complete buffered HTTP request into ``RequestData``."""

    try:
        raw_head, body = raw_request.split(_HEADER_TERMINATOR, maxsplit=1)
    except ValueError as exc:
        raise HTTPParseError("HTTP request is missing the header terminator.") from exc

    head = parse_request_head(raw_head + _HEADER_TERMINATOR)
    if len(body) != head.content_length:
        raise HTTPParseError(
            f"Expected {head.content_length} bytes of body data, received {len(body)}."
        )

    return RequestData(
        method=head.method,
        path=head.path,
        query_string=head.query_string,
        headers=head.headers,
        body=body,
        http_version=head.http_version,
    )


def _parse_headers(raw_header_lines: list[bytes]) -> list[Header]:
    headers: list[Header] = []
    for line in raw_header_lines:
        if not line:
            continue
        if line[:1] in {b" ", b"\t"}:
            raise HTTPParseError("Folded headers are not supported.")
        if b":" not in line:
            raise HTTPParseError("Malformed HTTP header line.")
        name, value = line.split(b":", maxsplit=1)
        name = name.strip().lower()
        value = value.strip()
        if not name:
            raise HTTPParseError("HTTP header name cannot be empty.")
        headers.append((name, value))
    return headers


def _extract_content_length(headers: list[Header]) -> int:
    values = [value for name, value in headers if name == b"content-length"]
    if not values:
        return 0
    if len(values) > 1:
        raise HTTPParseError("Duplicate Content-Length headers are not supported.")
    try:
        content_length = int(values[0].decode("ascii", errors="strict"))
    except ValueError as exc:
        raise HTTPParseError("Content-Length must be an integer.") from exc
    if content_length < 0:
        raise HTTPParseError("Content-Length cannot be negative.")
    return content_length
