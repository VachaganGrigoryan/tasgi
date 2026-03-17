"""Tests for the minimal ASGI server/runtime."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from tasgi.asgi_server import ASGIServer, ASGIServerError


def build_raw_request(path: str = "/") -> bytes:
    return f"GET {path} HTTP/1.1\r\nHost: example.test\r\n\r\n".encode("ascii")


class ASGIServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_minimal_asgi_app_returns_200(self) -> None:
        async def app(scope, receive, send) -> None:
            self.assertEqual(scope["type"], "http")
            await receive()
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send(
                {"type": "http.response.body", "body": b"ok", "more_body": False}
            )

        response = await ASGIServer(app).handle_raw_request(build_raw_request())
        self.assertTrue(response.startswith(b"HTTP/1.1 200 OK"))

    async def test_headers_are_serialized_correctly(self) -> None:
        async def app(scope, receive, send) -> None:
            await receive()
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"text/plain"),
                        (b"x-demo", b"1"),
                    ],
                }
            )
            await send(
                {"type": "http.response.body", "body": b"body", "more_body": False}
            )

        response = await ASGIServer(app).handle_raw_request(build_raw_request())
        self.assertIn(b"content-type: text/plain\r\n", response)
        self.assertIn(b"x-demo: 1\r\n", response)
        self.assertIn(b"content-length: 4\r\n", response)

    async def test_body_is_returned_correctly(self) -> None:
        async def app(scope, receive, send) -> None:
            await receive()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send(
                {
                    "type": "http.response.body",
                    "body": b"payload",
                    "more_body": False,
                }
            )

        response = await ASGIServer(app).handle_raw_request(build_raw_request("/body"))
        self.assertTrue(response.endswith(b"\r\n\r\npayload"))

    async def test_empty_final_body_is_still_a_complete_response(self) -> None:
        async def app(scope, receive, send) -> None:
            await receive()
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send(
                {
                    "type": "http.response.body",
                    "body": b"",
                    "more_body": False,
                }
            )

        response = await ASGIServer(app).handle_raw_request(build_raw_request("/empty"))
        self.assertTrue(response.startswith(b"HTTP/1.1 204 No Content"))
        self.assertTrue(response.endswith(b"\r\n\r\n"))

    async def test_unsupported_message_types_raise_clear_errors(self) -> None:
        async def app(scope, receive, send) -> None:
            await receive()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.trailers", "headers": []})

        with self.assertRaisesRegex(ASGIServerError, "Unsupported ASGI message type"):
            await ASGIServer(app).handle_raw_request(build_raw_request("/bad"))

    async def test_handler_exception_is_not_masked_by_incomplete_response_error(self) -> None:
        async def app(scope, receive, send) -> None:
            await receive()
            raise RuntimeError("boom")

        with self.assertRaisesRegex(RuntimeError, "boom"):
            await ASGIServer(app).handle_raw_request(build_raw_request("/boom"))

    async def test_response_stays_complete_if_app_raises_after_final_body(self) -> None:
        async def app(scope, receive, send) -> None:
            await receive()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send(
                {
                    "type": "http.response.body",
                    "body": b"done",
                    "more_body": False,
                }
            )
            raise RuntimeError("after response")

        response = await ASGIServer(app).handle_raw_request(build_raw_request("/after"))
        self.assertTrue(response.endswith(b"\r\n\r\ndone"))

    async def test_writer_failure_cancels_app_task(self) -> None:
        cancelled = asyncio.Event()

        async def app(scope, receive, send) -> None:
            try:
                await receive()
                await send({"type": "http.response.trailers", "headers": []})
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        with self.assertRaisesRegex(ASGIServerError, "Unsupported ASGI message type"):
            await asyncio.wait_for(
                ASGIServer(app).handle_raw_request(build_raw_request("/cancel")),
                timeout=0.5,
            )
        self.assertTrue(cancelled.is_set())


if __name__ == "__main__":
    unittest.main()
