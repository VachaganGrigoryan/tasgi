"""Minimal ASGI HTTP server with HTTP/1.1 and HTTP/2 transport support."""

from __future__ import annotations

import asyncio
import sys
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Awaitable, Callable, Optional, Protocol, Tuple

from .http2 import (
    CLIENT_CONNECTION_PREFACE,
    HTTP2Connection,
    FLAG_ACK,
    FRAME_SETTINGS,
    HTTP2ProtocolError,
    encode_data_frame,
    encode_headers_frame,
    encode_settings_frame,
    read_frame,
)
from .http_parser import HTTPParseError, parse_http_request, parse_request_head
from .types import ASGIApp, ASGIMessage, ASGIScope, Header, Receive, RequestData, Send
from .wsproto import (
    OPCODE_BINARY,
    OPCODE_CLOSE,
    OPCODE_PING,
    OPCODE_PONG,
    OPCODE_TEXT,
    WebSocketProtocolError,
    build_accept_token,
    build_handshake_response,
    build_rejection_response,
    decode_close_payload,
    encode_close_payload,
    encode_frame as encode_websocket_frame,
    is_websocket_upgrade,
    read_frame as read_websocket_frame,
)


class ASGIServerError(RuntimeError):
    """Raised when the ASGI app emits an invalid response sequence."""


class WritableTransport(Protocol):
    """Small transport protocol so tests can swap the writer implementation."""

    def write(self, data: bytes) -> None:
        """Write serialized response bytes."""

    async def drain(self) -> None:
        """Flush any buffered writes."""


REQUEST_BODY_CHUNK_SIZE = 64 * 1024


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

    async def send_response_start(self, stream_id: int, status: int, headers: list[Header]) -> None:
        """Write only the HTTP/2 response HEADERS frame for one stream."""

        normalized_headers = _normalize_response_headers(headers)
        response_headers = [(b":status", str(status).encode("ascii"))] + normalized_headers
        async with self.write_lock:
            self.transport.write(encode_headers_frame(stream_id, response_headers, end_stream=False))
            await self.transport.drain()

    async def send_response_body(self, stream_id: int, body: bytes, *, end_stream: bool) -> None:
        """Write one HTTP/2 DATA frame for an existing stream response."""

        async with self.write_lock:
            self.transport.write(encode_data_frame(stream_id, body, end_stream=end_stream))
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
        """Dispatch one socket connection as HTTP/1.1, HTTP/2, or WebSocket."""

        transport = _AsyncioStreamTransport(writer)
        client = writer.get_extra_info("peername")
        server_info = writer.get_extra_info("sockname")
        config = getattr(self.app, "config", None)
        http2_enabled = True if config is None else bool(getattr(config, "http2", True))
        is_http2 = False
        is_websocket = False
        try:
            if http2_enabled:
                is_http2, initial_bytes = await self._read_connection_prefix(reader)
            else:
                is_http2 = False
                initial_bytes = b""
            if is_http2:
                await self.handle_http2_connection(
                    reader,
                    client=client,
                    server=server_info,
                    transport=transport,
                    preface_already_read=True,
                )
            else:
                raw_request, extra_bytes = await self._read_raw_request_parts(
                    reader,
                    initial_bytes=initial_bytes,
                )
                request = parse_http_request(raw_request)
                if is_websocket_upgrade(request):
                    is_websocket = True
                    await self.handle_websocket_request(
                        request,
                        reader,
                        client=client,
                        server=server_info,
                        transport=transport,
                        initial_bytes=extra_bytes,
                    )
                else:
                    await self.handle_http_request(
                        request,
                        client=client,
                        server=server_info,
                        transport=transport,
                    )
        except HTTPParseError as exc:
            if not is_http2 and not is_websocket:
                transport.write(_build_error_response(400, str(exc)))
                await transport.drain()
        except HTTP2ProtocolError as exc:
            self._debug_http2_protocol_error(exc, client=client)
        except WebSocketProtocolError:
            pass
        except Exception:
            if not is_http2 and not is_websocket:
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

    async def handle_websocket_bytes(
        self,
        raw_request: bytes,
        frame_bytes: bytes = b"",
        *,
        client: Optional[Tuple[str, int]] = None,
        server: Optional[Tuple[str, int]] = None,
    ) -> bytes:
        """Handle one in-memory WebSocket upgrade request for compatibility tests."""

        request = parse_http_request(raw_request)
        reader = asyncio.StreamReader()
        if frame_bytes:
            reader.feed_data(frame_bytes)
        reader.feed_eof()
        transport = BufferingTransport()
        await self.handle_websocket_request(
            request,
            reader,
            client=client,
            server=server,
            transport=transport,
        )
        return transport.getvalue()

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

    async def handle_websocket_request(
        self,
        request: RequestData,
        reader: asyncio.StreamReader,
        *,
        client: Optional[Tuple[str, int]] = None,
        server: Optional[Tuple[str, int]] = None,
        transport: Optional[WritableTransport] = None,
        initial_bytes: bytes = b"",
    ) -> bytes:
        """Handle one HTTP/1.1 WebSocket upgrade request."""

        if not is_websocket_upgrade(request):
            raise WebSocketProtocolError("Request is not a supported WebSocket upgrade.")

        active_transport = transport or BufferingTransport()
        websocket_key = _require_header(request.headers, b"sec-websocket-key").decode("ascii")
        scope = self._build_websocket_scope(request, client=client, server=server)
        receive_queue: asyncio.Queue[ASGIMessage] = asyncio.Queue()
        await receive_queue.put({"type": "websocket.connect"})

        accepted = False
        closed = False

        merged_reader = asyncio.StreamReader()
        if initial_bytes:
            merged_reader.feed_data(initial_bytes)

        async def receive() -> ASGIMessage:
            return await receive_queue.get()

        async def send(message: ASGIMessage) -> None:
            nonlocal accepted, closed
            message_type = message.get("type")

            if message_type == "websocket.accept":
                if accepted:
                    raise ASGIServerError("Received duplicate websocket.accept message.")
                response = build_handshake_response(
                    build_accept_token(websocket_key),
                    subprotocol=message.get("subprotocol"),
                    headers=list(message.get("headers", [])),
                )
                active_transport.write(response)
                await active_transport.drain()
                accepted = True
                return

            if message_type == "websocket.send":
                if not accepted:
                    raise ASGIServerError("Received websocket.send before websocket.accept.")
                if closed:
                    raise ASGIServerError("Received websocket.send after websocket.close.")
                text = message.get("text")
                binary = message.get("bytes")
                if text is not None and binary is not None:
                    raise ASGIServerError("WebSocket send messages must use text or bytes, not both.")
                if text is not None:
                    payload = str(text).encode("utf-8")
                    active_transport.write(encode_websocket_frame(OPCODE_TEXT, payload))
                elif binary is not None:
                    active_transport.write(encode_websocket_frame(OPCODE_BINARY, bytes(binary)))
                else:
                    raise ASGIServerError("WebSocket send messages must include text or bytes.")
                await active_transport.drain()
                return

            if message_type == "websocket.close":
                close_code = int(message.get("code", 1000))
                reason = str(message.get("reason", ""))
                if not accepted:
                    active_transport.write(build_rejection_response())
                    await active_transport.drain()
                    closed = True
                    return
                if not closed:
                    active_transport.write(
                        encode_websocket_frame(
                            OPCODE_CLOSE,
                            encode_close_payload(close_code, reason),
                        )
                    )
                    await active_transport.drain()
                    closed = True
                return

            raise ASGIServerError(f"Unsupported ASGI message type: {message_type!r}")

        pump_task = asyncio.create_task(self._pump_reader(reader, merged_reader))
        reader_task = asyncio.create_task(
            self._read_websocket_messages(
                merged_reader,
                active_transport,
                receive_queue,
                lambda: accepted,
                lambda: closed,
            )
        )
        app_task = asyncio.create_task(self.app(scope, receive, send))

        try:
            await app_task
            if not closed:
                await send({"type": "websocket.close", "code": 1000, "reason": ""})
        except Exception:
            if not closed:
                try:
                    await send({"type": "websocket.close", "code": 1011, "reason": ""})
                except Exception:
                    pass
            raise
        finally:
            await _cancel_task(reader_task)
            await asyncio.gather(reader_task, return_exceptions=True)
            await _cancel_task(pump_task)
            await asyncio.gather(pump_task, return_exceptions=True)

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
        else:
            preface = CLIENT_CONNECTION_PREFACE

        connection = HTTP2Connection()
        connection.validate_client_preface(preface)
        connection_writer = _HTTP2ConnectionWriter(active_transport)
        await connection_writer.send_settings()

        stream_tasks: set[asyncio.Task[None]] = set()

        try:
            while True:
                try:
                    frame = await read_frame(reader)
                except asyncio.IncompleteReadError as exc:
                    if exc.partial:
                        raise HTTP2ProtocolError("Incomplete HTTP/2 frame received.")
                    break

                if frame.frame_type == FRAME_SETTINGS and not (frame.flags & FLAG_ACK):
                    await connection_writer.send_settings_ack()
                completed_stream = connection.handle_frame(frame)
                if completed_stream is not None:
                    self._start_http2_stream(
                        stream_tasks,
                        completed_stream,
                        connection_writer,
                        client=client,
                        server=server,
                    )
        finally:
            if connection.streams:
                for task in stream_tasks:
                    task.cancel()

        if connection.streams:
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
        stream_state,
        connection_writer: _HTTP2ConnectionWriter,
        *,
        client: Optional[Tuple[str, int]],
        server: Optional[Tuple[str, int]],
    ) -> None:
        request = stream_state.to_request_data()
        task = asyncio.create_task(
            self._handle_http2_stream(
                stream_state.stream_id,
                request,
                connection_writer,
                client=client,
                server=server,
            )
        )
        stream_tasks.add(task)
        task.add_done_callback(stream_tasks.discard)

    def _debug_http2_protocol_error(
        self,
        exc: HTTP2ProtocolError,
        *,
        client: Optional[Tuple[str, int]] = None,
    ) -> None:
        """Print HTTP/2 protocol failures in debug mode for local diagnosis."""

        config = getattr(self.app, "config", None)
        if config is None or not getattr(config, "debug", False):
            return

        location = ""
        if client is not None:
            location = " from %s:%s" % client
        print("HTTP/2 protocol error%s: %s" % (location, exc), file=sys.stderr, flush=True)

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

    async def _read_websocket_messages(
        self,
        reader: asyncio.StreamReader,
        transport: WritableTransport,
        queue: asyncio.Queue[ASGIMessage],
        is_accepted: Callable[[], bool],
        is_closed: Callable[[], bool],
    ) -> None:
        while True:
            try:
                frame = await read_websocket_frame(reader)
            except asyncio.IncompleteReadError:
                await queue.put({"type": "websocket.disconnect", "code": 1006})
                return
            except WebSocketProtocolError:
                await queue.put({"type": "websocket.disconnect", "code": 1002})
                return

            if frame.opcode == OPCODE_TEXT:
                await queue.put(
                    {
                        "type": "websocket.receive",
                        "text": frame.payload.decode("utf-8"),
                    }
                )
                continue

            if frame.opcode == OPCODE_BINARY:
                await queue.put({"type": "websocket.receive", "bytes": frame.payload})
                continue

            if frame.opcode == OPCODE_PING:
                if is_accepted() and not is_closed():
                    transport.write(encode_websocket_frame(OPCODE_PONG, frame.payload))
                    await transport.drain()
                continue

            if frame.opcode == OPCODE_CLOSE:
                code, _ = decode_close_payload(frame.payload)
                if is_accepted() and not is_closed():
                    transport.write(encode_websocket_frame(OPCODE_CLOSE, frame.payload))
                    await transport.drain()
                await queue.put({"type": "websocket.disconnect", "code": code})
                return

            raise WebSocketProtocolError("Unsupported WebSocket opcode %d." % frame.opcode)

    async def _pump_reader(
        self,
        source: asyncio.StreamReader,
        destination: asyncio.StreamReader,
    ) -> None:
        while True:
            chunk = await source.read(65_536)
            if not chunk:
                destination.feed_eof()
                return
            destination.feed_data(chunk)

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

    async def _read_raw_request_parts(
        self,
        reader: asyncio.StreamReader,
        *,
        initial_bytes: bytes = b"",
    ) -> tuple[bytes, bytes]:
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
        return raw_head + body[: head.content_length], body[head.content_length :]

    async def _read_raw_request(
        self,
        reader: asyncio.StreamReader,
        *,
        initial_bytes: bytes = b"",
    ) -> bytes:
        raw_request, _ = await self._read_raw_request_parts(reader, initial_bytes=initial_bytes)
        return raw_request

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

    def _build_websocket_scope(
        self,
        request: RequestData,
        *,
        client: Optional[Tuple[str, int]],
        server: Optional[Tuple[str, int]],
    ) -> ASGIScope:
        return {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": request.http_version,
            "scheme": "ws" if request.scheme == "http" else "wss",
            "path": request.path,
            "raw_path": request.path.encode("ascii"),
            "query_string": request.query_string,
            "headers": list(request.headers),
            "client": client,
            "server": server,
            "subprotocols": [],
        }

    def _build_receive(self, request: RequestData) -> Receive:
        body = request.body
        offset = 0
        delivered_disconnect = False

        async def receive() -> ASGIMessage:
            nonlocal offset, delivered_disconnect
            if offset < len(body) or (offset == 0 and body == b""):
                chunk = body[offset : offset + REQUEST_BODY_CHUNK_SIZE]
                offset += len(chunk)
                more_body = offset < len(body)
                if body == b"":
                    offset = 1
                    more_body = False
                return {
                    "type": "http.request",
                    "body": chunk,
                    "more_body": more_body,
                }
            if delivered_disconnect:
                return {"type": "http.disconnect"}
            delivered_disconnect = True
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
        start_message: Optional[ASGIMessage] = None
        headers_written = False
        chunked = False
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

            if message_type != "http.response.body":
                raise ASGIServerError(f"Unsupported ASGI message type: {message_type!r}")
            if start_message is None:
                raise ASGIServerError("Received http.response.body before http.response.start.")

            body = _coerce_response_body(message.get("body", b""))
            more_body = bool(message.get("more_body", False))
            status = int(start_message["status"])
            headers = list(start_message.get("headers", []))

            if not headers_written:
                if _has_header(headers, b"content-length"):
                    transport.write(serialize_http_response_head(status, headers))
                    headers_written = True
                elif not more_body:
                    transport.write(serialize_http_response(status, headers, body))
                    await transport.drain()
                    response_complete = True
                    break
                else:
                    transport.write(
                        serialize_http_response_head(
                            status,
                            headers + [(b"transfer-encoding", b"chunked")],
                        )
                    )
                    headers_written = True
                    chunked = True
                    await transport.drain()

            if chunked:
                if body:
                    transport.write(_encode_chunked_http1_body(body))
                if not more_body:
                    transport.write(_encode_chunked_http1_end())
                    await transport.drain()
                    response_complete = True
                    break
                if body:
                    await transport.drain()
                continue

            if body:
                transport.write(body)
            await transport.drain()
            if not more_body:
                response_complete = True
                break

        if start_message is None or not response_complete:
            raise ASGIServerError("ASGI app did not send a complete HTTP response.")

    async def _drain_http2_response_messages(
        self,
        queue: asyncio.Queue[Optional[ASGIMessage]],
        connection_writer: _HTTP2ConnectionWriter,
        stream_id: int,
    ) -> None:
        start_message: Optional[ASGIMessage] = None
        headers_written = False
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
                    raise ASGIServerError("Received http.response.body before http.response.start.")

                body = _coerce_response_body(message.get("body", b""))
                more_body = bool(message.get("more_body", False))
                status = int(start_message["status"])
                headers = list(start_message.get("headers", []))

                if not headers_written:
                    if not more_body and not _has_header(headers, b"content-length"):
                        headers = headers + [(b"content-length", str(len(body)).encode("ascii"))]
                    await connection_writer.send_response_start(stream_id, status, headers)
                    headers_written = True

                await connection_writer.send_response_body(stream_id, body, end_stream=not more_body)
                if not more_body:
                    response_complete = True
                    break
                continue

            raise ASGIServerError(f"Unsupported ASGI message type: {message_type!r}")

        if start_message is None or not response_complete:
            raise ASGIServerError("ASGI app did not send a complete HTTP response.")


def serialize_http_response(status: int, headers: list[Header], body: bytes) -> bytes:
    """Serialize an HTTP/1.1 response from ASGI-style fields."""

    normalized_headers = _normalize_response_headers(headers)
    if not _has_header(normalized_headers, b"content-length"):
        normalized_headers.append((b"content-length", str(len(body)).encode("ascii")))

    return serialize_http_response_head(status, normalized_headers) + body


def serialize_http_response_head(status: int, headers: list[Header]) -> bytes:
    """Serialize only the HTTP/1.1 response head."""

    normalized_headers = _normalize_response_headers(headers)
    reason = _http_reason_phrase(status)
    head_lines = [f"HTTP/1.1 {status} {reason}".encode("ascii")]
    head_lines.extend(name + b": " + value for name, value in normalized_headers)
    return b"\r\n".join(head_lines) + b"\r\n\r\n"


def _http_reason_phrase(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Unknown"


def _normalize_response_headers(headers: list[Header]) -> list[Header]:
    normalized: list[Header] = []
    for name, value in headers:
        if not isinstance(name, bytes) or not isinstance(value, bytes):
            raise ASGIServerError("Response headers must be bytes pairs.")
        normalized.append((name.lower(), value))
    return normalized


def _has_header(headers: list[Header], name: bytes) -> bool:
    lowered_name = name.lower()
    return any(header_name == lowered_name for header_name, _ in headers)


def _coerce_response_body(body) -> bytes:
    if not isinstance(body, (bytes, bytearray)):
        raise ASGIServerError("Response body must be bytes.")
    return bytes(body)


def _encode_chunked_http1_body(body: bytes) -> bytes:
    return f"{len(body):X}".encode("ascii") + b"\r\n" + body + b"\r\n"


def _encode_chunked_http1_end() -> bytes:
    return b"0\r\n\r\n"


def _require_header(headers: list[Header], name: bytes) -> bytes:
    lowered_name = name.lower()
    for header_name, value in headers:
        if header_name == lowered_name:
            return value
    raise WebSocketProtocolError("Missing required WebSocket header %s." % name.decode("ascii"))


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
