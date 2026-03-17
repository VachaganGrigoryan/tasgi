"""Minimal ASGI HTTP server with HTTP/1.1 and HTTP/2 transport support."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Awaitable, Callable, Optional, Protocol, Tuple

from .http2 import (
    CLIENT_CONNECTION_PREFACE,
    FLAG_ACK,
    FLAG_END_HEADERS,
    FLAG_END_STREAM,
    FRAME_DATA,
    FRAME_HEADERS,
    FRAME_SETTINGS,
    FRAME_WINDOW_UPDATE,
    HTTP2ProtocolError,
    decode_header_block,
    encode_data_frame,
    encode_headers_frame,
    encode_settings_frame,
    read_frame,
    request_data_from_headers,
)
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


@dataclass
class _HTTP2StreamState:
    headers: list[Header]
    body: bytearray = field(default_factory=bytearray)


@dataclass
class _HTTP2ConnectionWriter:
    transport: WritableTransport
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send_settings(self) -> None:
        """Send the server connection settings frame."""

        await self._write_bytes(encode_settings_frame())

    async def send_settings_ack(self) -> None:
        """Acknowledge peer settings."""

        await self._write_bytes(encode_settings_frame(ack=True))

    async def send_response(self, stream_id: int, status: int, headers: list[Header], body: bytes) -> None:
        """Write one complete HTTP/2 response for a single stream."""

        normalized_headers = _normalize_response_headers(headers)
        if not any(name == b"content-length" for name, _ in normalized_headers):
            normalized_headers.append((b"content-length", str(len(body)).encode("ascii")))

        response_headers = [(b":status", str(status).encode("ascii"))] + normalized_headers
        headers_frame = encode_headers_frame(stream_id, response_headers, end_stream=False)
        data_frame = encode_data_frame(stream_id, body, end_stream=True)
        async with self.write_lock:
            self.transport.write(headers_frame)
            self.transport.write(data_frame)
            await self.transport.drain()

    async def _write_bytes(self, data: bytes) -> None:
        async with self.write_lock:
            self.transport.write(data)
            await self.transport.drain()


class ASGIServer:
    """Tiny ASGI HTTP server with queued response bridges for HTTP/1.1 and HTTP/2."""

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
        """Dispatch one socket connection as HTTP/1.1 or HTTP/2."""

        transport = _AsyncioStreamTransport(writer)
        client = writer.get_extra_info("peername")
        server_info = writer.get_extra_info("sockname")
        is_http2 = False
        try:
            is_http2, initial_bytes = await self._read_connection_prefix(reader)
            if is_http2:
                await self.handle_http2_connection(
                    reader,
                    client=client,
                    server=server_info,
                    transport=transport,
                    preface_already_read=True,
                )
            else:
                raw_request = await self._read_raw_request(reader, initial_bytes=initial_bytes)
                await self.handle_raw_request(
                    raw_request,
                    client=client,
                    server=server_info,
                    transport=transport,
                )
        except HTTPParseError as exc:
            if not is_http2:
                transport.write(_build_error_response(400, str(exc)))
                await transport.drain()
        except HTTP2ProtocolError:
            pass
        except Exception:
            if not is_http2:
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
        """Parse raw HTTP/1.1 bytes and dispatch them through the ASGI runtime."""

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
        """Dispatch one buffered HTTP request and serialize an HTTP/1.1 response."""

        active_transport = transport or BufferingTransport()
        scope = self._build_scope(request, client=client, server=server)
        receive = self._build_receive(request)
        await self._execute_asgi_transaction(
            scope,
            receive,
            lambda queue: self._drain_http1_response_messages(queue, active_transport),
        )

        if isinstance(active_transport, BufferingTransport):
            return active_transport.getvalue()
        return b""

    async def handle_http2_bytes(
        self,
        raw_bytes: bytes,
        *,
        client: Optional[Tuple[str, int]] = None,
        server: Optional[Tuple[str, int]] = None,
    ) -> bytes:
        """Handle one in-memory HTTP/2 byte stream for compatibility tests."""

        reader = asyncio.StreamReader()
        reader.feed_data(raw_bytes)
        reader.feed_eof()
        transport = BufferingTransport()
        await self.handle_http2_connection(
            reader,
            client=client,
            server=server,
            transport=transport,
            preface_already_read=False,
        )
        return transport.getvalue()

    async def handle_http2_connection(
        self,
        reader: asyncio.StreamReader,
        *,
        client: Optional[Tuple[str, int]] = None,
        server: Optional[Tuple[str, int]] = None,
        transport: Optional[WritableTransport] = None,
        preface_already_read: bool = False,
    ) -> bytes:
        """Handle a cleartext HTTP/2 connection with one ASGI scope per stream."""

        active_transport = transport or BufferingTransport()
        if not preface_already_read:
            preface = await reader.readexactly(len(CLIENT_CONNECTION_PREFACE))
            if preface != CLIENT_CONNECTION_PREFACE:
                raise HTTP2ProtocolError("Missing HTTP/2 client connection preface.")

        connection_writer = _HTTP2ConnectionWriter(active_transport)
        await connection_writer.send_settings()

        stream_states: dict[int, _HTTP2StreamState] = {}
        stream_tasks: set[asyncio.Task[None]] = set()

        try:
            while True:
                try:
                    frame = await read_frame(reader)
                except asyncio.IncompleteReadError as exc:
                    if exc.partial:
                        raise HTTP2ProtocolError("Incomplete HTTP/2 frame received.")
                    break

                if frame.frame_type == FRAME_SETTINGS:
                    if frame.stream_id != 0:
                        raise HTTP2ProtocolError("HTTP/2 SETTINGS frames must use stream 0.")
                    if frame.flags & FLAG_ACK:
                        continue
                    await connection_writer.send_settings_ack()
                    continue

                if frame.frame_type == FRAME_WINDOW_UPDATE:
                    continue

                if frame.stream_id == 0:
                    raise HTTP2ProtocolError("HTTP/2 request frames must use a non-zero stream id.")

                if frame.frame_type == FRAME_HEADERS:
                    if not (frame.flags & FLAG_END_HEADERS):
                        raise HTTP2ProtocolError("CONTINUATION frames are not supported in prototype.")
                    if frame.stream_id in stream_states:
                        raise HTTP2ProtocolError("Repeated HEADERS frames are not supported in prototype.")
                    headers = decode_header_block(frame.payload)
                    stream_state = _HTTP2StreamState(headers=headers)
                    if frame.flags & FLAG_END_STREAM:
                        self._start_http2_stream(
                            stream_tasks,
                            frame.stream_id,
                            stream_state,
                            connection_writer,
                            client=client,
                            server=server,
                        )
                    else:
                        stream_states[frame.stream_id] = stream_state
                    continue

                if frame.frame_type == FRAME_DATA:
                    stream_state = stream_states.get(frame.stream_id)
                    if stream_state is None:
                        raise HTTP2ProtocolError("Received DATA for an unknown HTTP/2 stream.")
                    stream_state.body.extend(frame.payload)
                    if frame.flags & FLAG_END_STREAM:
                        self._start_http2_stream(
                            stream_tasks,
                            frame.stream_id,
                            stream_state,
                            connection_writer,
                            client=client,
                            server=server,
                        )
                        del stream_states[frame.stream_id]
                    continue

                raise HTTP2ProtocolError(
                    "Unsupported HTTP/2 frame type %d in prototype." % frame.frame_type
                )
        finally:
            if stream_states:
                for task in stream_tasks:
                    task.cancel()

        if stream_states:
            raise HTTP2ProtocolError("HTTP/2 connection closed with incomplete request streams.")

        if stream_tasks:
            results = await asyncio.gather(*stream_tasks, return_exceptions=True)
            for result in results:
                error = _task_exception(result)
                if error is not None:
                    raise error

        if isinstance(active_transport, BufferingTransport):
            return active_transport.getvalue()
        return b""

    def _start_http2_stream(
        self,
        stream_tasks: set[asyncio.Task[None]],
        stream_id: int,
        stream_state: _HTTP2StreamState,
        connection_writer: _HTTP2ConnectionWriter,
        *,
        client: Optional[Tuple[str, int]],
        server: Optional[Tuple[str, int]],
    ) -> None:
        request = request_data_from_headers(stream_state.headers, bytes(stream_state.body))
        task = asyncio.create_task(
            self._handle_http2_stream(
                stream_id,
                request,
                connection_writer,
                client=client,
                server=server,
            )
        )
        stream_tasks.add(task)
        task.add_done_callback(stream_tasks.discard)

    async def _handle_http2_stream(
        self,
        stream_id: int,
        request: RequestData,
        connection_writer: _HTTP2ConnectionWriter,
        *,
        client: Optional[Tuple[str, int]],
        server: Optional[Tuple[str, int]],
    ) -> None:
        scope = self._build_scope(request, client=client, server=server)
        receive = self._build_receive(request)
        await self._execute_asgi_transaction(
            scope,
            receive,
            lambda queue: self._drain_http2_response_messages(queue, connection_writer, stream_id),
        )

    async def _execute_asgi_transaction(
        self,
        scope: ASGIScope,
        receive: Receive,
        response_writer: Callable[[asyncio.Queue[Optional[ASGIMessage]]], Awaitable[None]],
    ) -> None:
        response_messages: asyncio.Queue[Optional[ASGIMessage]] = asyncio.Queue()
        send = self._build_send(response_messages)

        app_task = asyncio.create_task(
            self._run_app_and_close_queue(scope, receive, send, response_messages)
        )
        writer_task = asyncio.create_task(response_writer(response_messages))
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

            await _cancel_task(app_task)
            await asyncio.gather(app_task, return_exceptions=True)
            return

        app_result = await asyncio.gather(app_task, return_exceptions=True)
        writer_result = await asyncio.gather(writer_task, return_exceptions=True)
        app_error = _task_exception(app_result[0])
        writer_error = _task_exception(writer_result[0])
        if writer_error is not None:
            if app_error is not None:
                raise app_error
            raise writer_error
        if app_error is not None:
            return

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

    async def _read_connection_prefix(self, reader: asyncio.StreamReader) -> tuple[bool, bytes]:
        buffer = bytearray()
        while len(buffer) < len(CLIENT_CONNECTION_PREFACE):
            chunk = await reader.read(1)
            if not chunk:
                break
            buffer.extend(chunk)
            if not CLIENT_CONNECTION_PREFACE.startswith(buffer):
                return False, bytes(buffer)
            if bytes(buffer) == CLIENT_CONNECTION_PREFACE:
                return True, bytes(buffer)
        return bytes(buffer) == CLIENT_CONNECTION_PREFACE, bytes(buffer)

    async def _read_raw_request(
        self,
        reader: asyncio.StreamReader,
        *,
        initial_bytes: bytes = b"",
    ) -> bytes:
        buffer = bytearray(initial_bytes)
        delimiter = b"\r\n\r\n"

        while delimiter not in buffer:
            chunk = await reader.read(65_536)
            if not chunk:
                raise HTTPParseError("Incomplete HTTP request head.")
            buffer.extend(chunk)

        head_bytes, _, rest = bytes(buffer).partition(delimiter)
        raw_head = head_bytes + delimiter
        head = parse_request_head(raw_head)
        body = rest
        if len(body) < head.content_length:
            body += await reader.readexactly(head.content_length - len(body))
        return raw_head + body[: head.content_length]

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
            "scheme": request.scheme,
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

    async def _drain_http1_response_messages(
        self,
        queue: asyncio.Queue[Optional[ASGIMessage]],
        transport: WritableTransport,
    ) -> None:
        status, headers, body = await self._collect_complete_response(queue)
        transport.write(serialize_http_response(status, headers, body))
        await transport.drain()

    async def _drain_http2_response_messages(
        self,
        queue: asyncio.Queue[Optional[ASGIMessage]],
        connection_writer: _HTTP2ConnectionWriter,
        stream_id: int,
    ) -> None:
        status, headers, body = await self._collect_complete_response(queue)
        await connection_writer.send_response(stream_id, status, headers, body)

    async def _collect_complete_response(
        self,
        queue: asyncio.Queue[Optional[ASGIMessage]],
    ) -> tuple[int, list[Header], bytes]:
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

        return (
            int(start_message["status"]),
            list(start_message.get("headers", [])),
            b"".join(body_parts),
        )


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
