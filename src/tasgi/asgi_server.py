"""Minimal ASGI HTTP server and response transport bridge."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Optional, Protocol, Tuple

from .http_parser import HTTPParseError, parse_http_request, parse_request_head
from .types import ASGIApp, ASGIMessage, ASGIScope, Header, Receive, RequestData, Send


class ASGIServerError(RuntimeError):
    """Raised when the ASGI app emits an invalid response sequence."""


class WritableTransport(Protocol):
    """Small transport protocol so tests can swap the writer implementation."""

    def write(self, data: bytes) -> None:
        """Write serialized response bytes."""

    async def drain(self) -> None:
        """Flush any buffered writes."""


@dataclass
class BufferingTransport:
    """In-memory transport used by tests and internal request helpers."""

    buffer: bytearray = field(default_factory=bytearray)
    write_thread_ids: list[int] = field(default_factory=list)

    def write(self, data: bytes) -> None:
        """Store written bytes and record which thread performed the write."""

        self.write_thread_ids.append(threading.get_ident())
        self.buffer.extend(data)

    async def drain(self) -> None:
        """Mirror the stream-writer drain API for test transports."""

    def getvalue(self) -> bytes:
        """Return the accumulated HTTP response bytes."""

        return bytes(self.buffer)


@dataclass
class _AsyncioStreamTransport:
    writer: asyncio.StreamWriter

    def write(self, data: bytes) -> None:
        self.writer.write(data)

    async def drain(self) -> None:
        await self.writer.drain()


class ASGIServer:
    """Tiny ASGI HTTP/1.1 server with a queued response bridge."""

    def __init__(self, app: ASGIApp):
        """Store the ASGI app that will handle HTTP requests."""

        self.app = app

    async def serve(self, host: str = "127.0.0.1", port: int = 8000) -> None:
        """Start accepting TCP connections and serve forever."""

        lifespan = getattr(self.app, "lifespan", None)
        if lifespan is not None:
            timeout = getattr(getattr(self.app, "config", None), "graceful_shutdown_timeout", None)
            if timeout is None:
                async with lifespan():
                    server = await asyncio.start_server(self.handle_connection, host, port)
                    async with server:
                        await server.serve_forever()
            else:
                async with _timed_lifespan(lifespan, timeout):
                    server = await asyncio.start_server(self.handle_connection, host, port)
                    async with server:
                        await server.serve_forever()
            return

        startup = getattr(self.app, "startup", None)
        if startup is not None:
            await startup()
        server = await asyncio.start_server(self.handle_connection, host, port)
        try:
            async with server:
                await server.serve_forever()
        finally:
            shutdown = getattr(self.app, "shutdown", None)
            if shutdown is not None:
                timeout = getattr(getattr(self.app, "config", None), "graceful_shutdown_timeout", None)
                if timeout is None:
                    await shutdown()
                else:
                    await asyncio.wait_for(shutdown(), timeout=timeout)

    async def handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Read one buffered HTTP request, dispatch it, and close the socket."""

        transport = _AsyncioStreamTransport(writer)
        client = writer.get_extra_info("peername")
        server_info = writer.get_extra_info("sockname")
        try:
            raw_request = await self._read_raw_request(reader)
            await self.handle_raw_request(
                raw_request,
                client=client,
                server=server_info,
                transport=transport,
            )
        except HTTPParseError as exc:
            transport.write(_build_error_response(400, str(exc)))
            await transport.drain()
        except Exception:
            transport.write(_build_error_response(500, "Internal Server Error"))
            await transport.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def handle_raw_request(
        self,
        raw_request: bytes,
        *,
        client: Optional[Tuple[str, int]] = None,
        server: Optional[Tuple[str, int]] = None,
        transport: Optional[WritableTransport] = None,
    ) -> bytes:
        """Parse raw bytes and dispatch them through the ASGI runtime."""

        request = parse_http_request(raw_request)
        return await self.handle_http_request(
            request,
            client=client,
            server=server,
            transport=transport,
        )

    async def handle_http_request(
        self,
        request: RequestData,
        *,
        client: Optional[Tuple[str, int]] = None,
        server: Optional[Tuple[str, int]] = None,
        transport: Optional[WritableTransport] = None,
    ) -> bytes:
        """Dispatch a parsed request to the ASGI app and serialize its response."""

        active_transport = transport or BufferingTransport()
        scope = self._build_scope(request, client=client, server=server)
        response_messages: asyncio.Queue[Optional[ASGIMessage]] = asyncio.Queue()
        receive = self._build_receive(request)
        send = self._build_send(response_messages)

        # The event loop owns transport writes. Apps only enqueue ASGI messages.
        app_task = asyncio.create_task(
            self._run_app_and_close_queue(scope, receive, send, response_messages)
        )
        writer_task = asyncio.create_task(
            self._drain_response_messages(response_messages, active_transport)
        )
        done, _ = await asyncio.wait(
            {app_task, writer_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if writer_task in done:
            writer_result = await asyncio.gather(writer_task, return_exceptions=True)
            writer_error = _task_exception(writer_result[0])
            if writer_error is not None:
                await _cancel_task(app_task)
                app_result = await asyncio.gather(app_task, return_exceptions=True)
                app_error = _task_exception(app_result[0])
                if app_error is not None:
                    raise app_error
                raise writer_error

            # Once a complete response is committed, cancel leftover app work so the
            # request cannot leak background execution or emit a second response.
            await _cancel_task(app_task)
            await asyncio.gather(app_task, return_exceptions=True)
        else:
            app_result = await asyncio.gather(app_task, return_exceptions=True)
            writer_result = await asyncio.gather(writer_task, return_exceptions=True)
            app_error = _task_exception(app_result[0])
            writer_error = _task_exception(writer_result[0])
            if writer_error is not None:
                if app_error is not None:
                    raise app_error
                raise writer_error
            if app_error is not None:
                # The response was already completed successfully, so preserve it.
                pass

        if isinstance(active_transport, BufferingTransport):
            return active_transport.getvalue()
        return b""

    async def _run_app_and_close_queue(
        self,
        scope: ASGIScope,
        receive: Receive,
        send: Send,
        queue: asyncio.Queue[Optional[ASGIMessage]],
    ) -> None:
        try:
            await self.app(scope, receive, send)
        finally:
            await queue.put(None)

    async def _read_raw_request(self, reader: asyncio.StreamReader) -> bytes:
        raw_head = await reader.readuntil(b"\r\n\r\n")
        head = parse_request_head(raw_head)
        body = b""
        if head.content_length:
            body = await reader.readexactly(head.content_length)
        return raw_head + body

    def _build_scope(
        self,
        request: RequestData,
        *,
        client: Optional[Tuple[str, int]],
        server: Optional[Tuple[str, int]],
    ) -> ASGIScope:
        return {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": request.http_version,
            "method": request.method,
            "scheme": "http",
            "path": request.path,
            "raw_path": request.path.encode("ascii"),
            "query_string": request.query_string,
            "headers": list(request.headers),
            "client": client,
            "server": server,
        }

    def _build_receive(self, request: RequestData) -> Receive:
        delivered = False

        async def receive() -> ASGIMessage:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {
                    "type": "http.request",
                    "body": request.body,
                    "more_body": False,
                }
            return {"type": "http.disconnect"}

        return receive

    def _build_send(self, queue: asyncio.Queue[Optional[ASGIMessage]]) -> Send:
        sent_start = False
        response_complete = False

        async def send(message: ASGIMessage) -> None:
            nonlocal sent_start, response_complete
            message_type = message.get("type")
            if response_complete:
                raise ASGIServerError("Received ASGI message after final response body.")
            if message_type == "http.response.start":
                if sent_start:
                    raise ASGIServerError("Received duplicate http.response.start message.")
                sent_start = True
            elif message_type == "http.response.body":
                if not sent_start:
                    raise ASGIServerError(
                        "Received http.response.body before http.response.start."
                    )
                if not message.get("more_body", False):
                    response_complete = True
            await queue.put(message)

        return send

    async def _drain_response_messages(
        self,
        queue: asyncio.Queue[Optional[ASGIMessage]],
        transport: WritableTransport,
    ) -> None:
        start_message: Optional[ASGIMessage] = None
        body_parts: list[bytes] = []
        response_complete = False

        while True:
            message = await queue.get()
            if message is None:
                break

            message_type = message.get("type")
            if message_type == "http.response.start":
                if start_message is not None:
                    raise ASGIServerError("Received duplicate http.response.start message.")
                start_message = message
                continue

            if message_type == "http.response.body":
                if start_message is None:
                    raise ASGIServerError(
                        "Received http.response.body before http.response.start."
                    )
                body = message.get("body", b"")
                if not isinstance(body, (bytes, bytearray)):
                    raise ASGIServerError("Response body must be bytes.")
                body_parts.append(bytes(body))
                if not message.get("more_body", False):
                    response_complete = True
                    break
                continue

            raise ASGIServerError(f"Unsupported ASGI message type: {message_type!r}")

        if start_message is None or not response_complete:
            raise ASGIServerError("ASGI app did not send a complete HTTP response.")

        response_bytes = serialize_http_response(
            int(start_message["status"]),
            list(start_message.get("headers", [])),
            b"".join(body_parts),
        )
        transport.write(response_bytes)
        await transport.drain()


def serialize_http_response(status: int, headers: list[Header], body: bytes) -> bytes:
    """Serialize an HTTP/1.1 response from ASGI-style fields."""

    normalized_headers = _normalize_response_headers(headers)
    if not any(name == b"content-length" for name, _ in normalized_headers):
        normalized_headers.append((b"content-length", str(len(body)).encode("ascii")))

    try:
        reason = HTTPStatus(status).phrase
    except ValueError:
        reason = "Unknown"

    head_lines = [f"HTTP/1.1 {status} {reason}".encode("ascii")]
    head_lines.extend(name + b": " + value for name, value in normalized_headers)
    return b"\r\n".join(head_lines) + b"\r\n\r\n" + body


def _normalize_response_headers(headers: list[Header]) -> list[Header]:
    normalized: list[Header] = []
    for name, value in headers:
        if not isinstance(name, bytes) or not isinstance(value, bytes):
            raise ASGIServerError("Response headers must be bytes pairs.")
        normalized.append((name.lower(), value))
    return normalized


def _build_error_response(status: int, message: str) -> bytes:
    body = message.encode("utf-8")
    return serialize_http_response(
        status,
        [(b"content-type", b"text/plain; charset=utf-8")],
        body,
    )


def _task_exception(result) -> Optional[BaseException]:
    if isinstance(result, asyncio.CancelledError):
        return None
    if isinstance(result, BaseException):
        return result
    return None


async def _cancel_task(task: asyncio.Task) -> None:
    if task.done():
        return
    task.cancel()


class _timed_lifespan:
    def __init__(self, lifespan_factory, timeout: float):
        self._lifespan_factory = lifespan_factory
        self._timeout = timeout
        self._context = None

    async def __aenter__(self):
        self._context = self._lifespan_factory()
        return await self._context.__aenter__()

    async def __aexit__(self, exc_type, exc, tb):
        return await asyncio.wait_for(
            self._context.__aexit__(exc_type, exc, tb),
            timeout=self._timeout,
        )
