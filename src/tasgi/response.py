"""Response types exposed by tasgi."""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional, Union

from .types import ASGIMessage, Header

HeaderValue = tuple[Union[str, bytes], Union[str, bytes]]


class Response:
    """Buffered HTTP response that can be serialized to ASGI messages."""

    def __init__(
        self,
        body: Union[bytes, str] = b"",
        *,
        status_code: int = 200,
        headers: Optional[Iterable[HeaderValue]] = None,
        media_type: Optional[str] = None,
    ):
        self.status_code = status_code
        self.body = body.encode("utf-8") if isinstance(body, str) else bytes(body)
        self.headers = self._normalize_headers(headers)
        if media_type is not None:
            self.headers.append((b"content-type", media_type.encode("latin-1")))

    @property
    def status(self) -> int:
        """Compatibility alias for the HTTP status code."""

        return self.status_code

    @classmethod
    def text(
        cls,
        text: str,
        *,
        status_code: int = 200,
        headers: Optional[Iterable[HeaderValue]] = None,
    ) -> "Response":
        """Create a text response without importing ``TextResponse``."""

        return cls(
            text,
            status_code=status_code,
            headers=headers,
            media_type="text/plain; charset=utf-8",
        )

    @classmethod
    def json(
        cls,
        content: Any,
        *,
        status_code: int = 200,
        headers: Optional[Iterable[HeaderValue]] = None,
    ) -> "Response":
        """Create a JSON response without importing ``JsonResponse``."""

        return cls(
            json.dumps(content).encode("utf-8"),
            status_code=status_code,
            headers=headers,
            media_type="application/json",
        )

    def to_asgi_messages(self) -> list[ASGIMessage]:
        """Serialize the response into ASGI response messages."""

        return [
            {
                "type": "http.response.start",
                "status": self.status_code,
                "headers": list(self.headers),
            },
            {
                "type": "http.response.body",
                "body": self.body,
                "more_body": False,
            },
        ]

    @staticmethod
    def _normalize_headers(headers: Optional[Iterable[HeaderValue]]) -> list[Header]:
        normalized: list[Header] = []
        for name, value in list(headers or []):
            name_bytes = name if isinstance(name, bytes) else name.encode("latin-1")
            value_bytes = value if isinstance(value, bytes) else value.encode("latin-1")
            normalized.append((name_bytes.lower(), value_bytes))
        return normalized


class TextResponse(Response):
    """Convenience response for text/plain payloads."""

    def __init__(
        self,
        text: str,
        *,
        status_code: int = 200,
        headers: Optional[Iterable[HeaderValue]] = None,
    ):
        super().__init__(
            text,
            status_code=status_code,
            headers=headers,
            media_type="text/plain; charset=utf-8",
        )


class JsonResponse(Response):
    """Convenience response for JSON payloads."""

    def __init__(
        self,
        content: Any,
        *,
        status_code: int = 200,
        headers: Optional[Iterable[HeaderValue]] = None,
    ):
        super().__init__(
            json.dumps(content).encode("utf-8"),
            status_code=status_code,
            headers=headers,
            media_type="application/json",
        )
