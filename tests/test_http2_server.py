"""Basic compatibility tests for tasgi's HTTP/2 transport path."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from tasgi import JsonResponse, TasgiApp, TasgiConfig, TextResponse
from tasgi.asgi_server import ASGIServer
from tasgi.http2 import (
    CLIENT_CONNECTION_PREFACE,
    FLAG_ACK,
    FRAME_DATA,
    FRAME_HEADERS,
    FRAME_SETTINGS,
    decode_header_block,
    encode_data_frame,
    encode_headers_frame,
    encode_settings_frame,
)


def build_h2_request(
    stream_id: int,
    path: str,
    *,
    method: str = "GET",
    body: bytes = b"",
) -> bytes:
    headers = [
        (b":method", method.encode("ascii")),
        (b":path", path.encode("ascii")),
        (b":scheme", b"http"),
        (b":authority", b"example.test"),
    ]
    if method == "POST":
        headers.append((b"content-length", str(len(body)).encode("ascii")))

    request = encode_headers_frame(stream_id, headers, end_stream=(body == b""))
    if body:
        request += encode_data_frame(stream_id, body, end_stream=True)
    return request


def parse_h2_frames(data: bytes) -> list[tuple[int, int, int, bytes]]:
    frames: list[tuple[int, int, int, bytes]] = []
    index = 0
    while index < len(data):
        length = int.from_bytes(data[index : index + 3], "big")
        frame_type = data[index + 3]
        flags = data[index + 4]
        stream_id = int.from_bytes(data[index + 5 : index + 9], "big") & 0x7FFF_FFFF
        payload_start = index + 9
        payload_end = payload_start + length
        frames.append((frame_type, flags, stream_id, data[payload_start:payload_end]))
        index = payload_end
    return frames


def collect_h2_responses(data: bytes) -> dict[int, dict[str, object]]:
    responses: dict[int, dict[str, object]] = {}
    for frame_type, flags, stream_id, payload in parse_h2_frames(data):
        if frame_type == FRAME_SETTINGS:
            continue
        if frame_type == FRAME_HEADERS:
            headers = decode_header_block(payload)
            entry = responses.setdefault(stream_id, {"headers": [], "body": b""})
            entry["headers"] = headers
            entry["flags"] = flags
            continue
        if frame_type == FRAME_DATA:
            entry = responses.setdefault(stream_id, {"headers": [], "body": b""})
            entry["body"] = bytes(entry["body"]) + payload
    return responses


class HTTP2ServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_http2_stream_returns_complete_response(self) -> None:
        async def app(scope, receive, send) -> None:
            self.assertEqual(scope["http_version"], "2")
            self.assertEqual(scope["path"], "/h2")
            request = await receive()
            self.assertEqual(request["body"], b"")
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok", "more_body": False})

        raw_request = (
            CLIENT_CONNECTION_PREFACE
            + encode_settings_frame()
            + build_h2_request(1, "/h2")
        )
        response_bytes = await ASGIServer(app).handle_http2_bytes(raw_request)
        responses = collect_h2_responses(response_bytes)

        self.assertIn(1, responses)
        self.assertEqual(responses[1]["body"], b"ok")
        header_map = dict(responses[1]["headers"])
        self.assertEqual(header_map[b":status"], b"200")
        self.assertEqual(header_map[b"content-length"], b"2")

    async def test_http2_post_body_is_buffered_into_receive(self) -> None:
        async def app(scope, receive, send) -> None:
            request = await receive()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send(
                {
                    "type": "http.response.body",
                    "body": bytes(request["body"]),
                    "more_body": False,
                }
            )

        raw_request = (
            CLIENT_CONNECTION_PREFACE
            + encode_settings_frame()
            + build_h2_request(1, "/echo", method="POST", body=b"payload")
        )
        response_bytes = await ASGIServer(app).handle_http2_bytes(raw_request)
        responses = collect_h2_responses(response_bytes)

        self.assertEqual(responses[1]["body"], b"payload")

    async def test_multiple_http2_streams_map_to_independent_responses(self) -> None:
        async def app(scope, receive, send) -> None:
            if scope["path"] == "/slow":
                await asyncio.sleep(0.01)
            await receive()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send(
                {
                    "type": "http.response.body",
                    "body": scope["path"].encode("ascii"),
                    "more_body": False,
                }
            )

        raw_request = (
            CLIENT_CONNECTION_PREFACE
            + encode_settings_frame()
            + build_h2_request(1, "/slow")
            + build_h2_request(3, "/fast")
        )
        response_bytes = await ASGIServer(app).handle_http2_bytes(raw_request)
        responses = collect_h2_responses(response_bytes)

        self.assertEqual(responses[1]["body"], b"/slow")
        self.assertEqual(responses[3]["body"], b"/fast")

    async def test_tasgi_app_async_and_thread_handlers_work_over_http2(self) -> None:
        app = TasgiApp(config=TasgiConfig(default_execution="async", thread_pool_workers=4))

        @app.get("/async")
        async def async_route(request) -> JsonResponse:
            return JsonResponse({"mode": "async", "version": request.http_version})

        @app.get("/thread")
        def thread_route(request) -> TextResponse:
            return TextResponse("thread-%s" % request.http_version)

        raw_request = (
            CLIENT_CONNECTION_PREFACE
            + encode_settings_frame()
            + build_h2_request(1, "/async")
            + build_h2_request(3, "/thread")
        )
        try:
            response_bytes = await ASGIServer(app).handle_http2_bytes(raw_request)
        finally:
            await app.close()

        responses = collect_h2_responses(response_bytes)
        self.assertIn(b'"version": "2"', responses[1]["body"])
        self.assertEqual(responses[3]["body"], b"thread-2")

    async def test_http2_server_emits_settings_and_ack(self) -> None:
        async def app(scope, receive, send) -> None:
            await receive()
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b"", "more_body": False})

        raw_request = (
            CLIENT_CONNECTION_PREFACE
            + encode_settings_frame()
            + build_h2_request(1, "/empty")
        )
        response_bytes = await ASGIServer(app).handle_http2_bytes(raw_request)
        frames = parse_h2_frames(response_bytes)

        settings_frames = [frame for frame in frames if frame[0] == FRAME_SETTINGS]
        self.assertEqual(len(settings_frames), 2)
        self.assertEqual(settings_frames[0][1] & FLAG_ACK, 0)
        self.assertEqual(settings_frames[1][1] & FLAG_ACK, FLAG_ACK)


if __name__ == "__main__":
    unittest.main()
