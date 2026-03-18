"""Response types exposed by tasgi."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, AsyncIterable, AsyncIterator, Iterable, Optional, Union

from .types import ASGIMessage, Header

if TYPE_CHECKING:
    from .runtime import TasgiRuntime

HeaderValue = tuple[Union[str, bytes], Union[str, bytes]]
ChunkValue = Union[bytes, bytearray, str]
ChunkIterable = Union[Iterable[ChunkValue], AsyncIterable[ChunkValue]]
_STREAM_END = object()


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

    async def iter_asgi_messages(self) -> AsyncIterator[ASGIMessage]:
        """Yield ASGI response messages for this response."""

        for message in self.to_asgi_messages():
            yield message

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


class StreamingResponse(Response):
    """Streaming HTTP response built from a sync or async chunk iterable."""

    def __init__(
        self,
        content: ChunkIterable,
        *,
        status_code: int = 200,
        headers: Optional[Iterable[HeaderValue]] = None,
        media_type: Optional[str] = None,
    ):
        super().__init__(b"", status_code=status_code, headers=headers, media_type=media_type)
        self._content = content
        self._thread_runtime: Optional["TasgiRuntime"] = None

    def bind_thread_runtime(self, runtime: "TasgiRuntime") -> "StreamingResponse":
        """Bind sync chunk iteration to the tasgi worker runtime."""

        self._thread_runtime = runtime
        return self

    async def iter_asgi_messages(self) -> AsyncIterator[ASGIMessage]:
        """Yield response start plus one or more body messages."""

        yield {
            "type": "http.response.start",
            "status": self.status_code,
            "headers": list(self.headers),
        }

        async for chunk in self._iter_chunks():
            yield {
                "type": "http.response.body",
                "body": chunk,
                "more_body": True,
            }

        yield {
            "type": "http.response.body",
            "body": b"",
            "more_body": False,
        }

    async def _iter_chunks(self) -> AsyncIterator[bytes]:
        if hasattr(self._content, "__aiter__"):
            async for chunk in self._content:  # type: ignore[union-attr]
                yield _normalize_chunk(chunk)
            return

        if isinstance(self._content, (bytes, bytearray, str)):
            yield _normalize_chunk(self._content)
            return

        iterator = iter(self._content)  # type: ignore[arg-type]
        if self._thread_runtime is None:
            for chunk in iterator:
                yield _normalize_chunk(chunk)
            return

        while True:
            chunk = await self._thread_runtime.run_sync(_next_chunk, iterator)
            if chunk is _STREAM_END:
                break
            yield _normalize_chunk(chunk)


def _normalize_chunk(chunk: ChunkValue) -> bytes:
    if isinstance(chunk, str):
        return chunk.encode("utf-8")
    if isinstance(chunk, (bytes, bytearray)):
        return bytes(chunk)
    raise TypeError("Streaming response chunks must be bytes or text.")


def _next_chunk(iterator):
    try:
        return next(iterator)
    except StopIteration:
        return _STREAM_END
