"""Streaming request and response tests."""

from __future__ import annotations

import asyncio
import threading
import unittest

from support import build_get_request
from tasgi import StreamingResponse, TasgiApp, TasgiConfig
from tasgi.asgi_server import ASGIServer


class TasgiStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_streaming_response_is_sent_in_multiple_body_messages(self) -> None:
        app = TasgiApp()

        @app.route.get("/stream")
        async def stream_route(request) -> StreamingResponse:
            async def chunks():
                yield "hello "
                await asyncio.sleep(0)
                yield "world"

            return StreamingResponse(chunks())

        try:
            response = await ASGIServer(app).handle_raw_request(build_get_request("/stream"))
        finally:
            await app.close()

        self.assertIn(b"transfer-encoding: chunked\r\n", response)
        self.assertTrue(response.endswith(b"\r\n\r\n6\r\nhello \r\n5\r\nworld\r\n0\r\n\r\n"))

    async def test_threaded_streaming_response_iterates_in_worker_thread(self) -> None:
        app = TasgiApp(config=TasgiConfig(default_execution="thread"))
        loop_thread_id = threading.get_ident()
        generator_thread_ids: list[int] = []

        @app.route.get("/stream")
        def stream_route(request) -> StreamingResponse:
            def chunks():
                generator_thread_ids.append(threading.get_ident())
                yield b"thread "
                generator_thread_ids.append(threading.get_ident())
                yield b"stream"

            return StreamingResponse(chunks())

        try:
            response = await ASGIServer(app).handle_raw_request(build_get_request("/stream"))
        finally:
            await app.close()

        self.assertIn(b"thread", response)
        self.assertEqual(len(generator_thread_ids), 2)
        self.assertTrue(all(thread_id != loop_thread_id for thread_id in generator_thread_ids))
