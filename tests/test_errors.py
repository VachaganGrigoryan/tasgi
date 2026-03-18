"""Error handling tests."""

from __future__ import annotations

import unittest

from support import build_get_request
from tasgi import TasgiApp, TasgiConfig
from tasgi.asgi_server import ASGIServer, ASGIServerError


class TasgiErrorHandlingTests(unittest.IsolatedAsyncioTestCase):
    async def test_handler_exception_returns_generic_500_in_production_mode(self) -> None:
        app = TasgiApp(config=TasgiConfig(debug=False))

        @app.route.get("/error")
        def error_route(request):
            raise RuntimeError("boom")

        try:
            response = await ASGIServer(app).handle_raw_request(build_get_request("/error"))
        finally:
            await app.close()

        self.assertIn(b"HTTP/1.1 500 Internal Server Error", response)
        self.assertTrue(response.endswith(b"\r\n\r\nInternal Server Error"))

    async def test_handler_exception_returns_debug_text_when_debug_enabled(self) -> None:
        app = TasgiApp(config=TasgiConfig(debug=True))

        @app.route.get("/error")
        def error_route(request):
            raise RuntimeError("boom")

        try:
            response = await ASGIServer(app).handle_raw_request(build_get_request("/error"))
        finally:
            await app.close()

        self.assertIn(b"Internal Server Error: RuntimeError: boom on GET /error", response)

    async def test_invalid_handler_return_still_produces_complete_500_response(self) -> None:
        app = TasgiApp(config=TasgiConfig(debug=False))

        @app.route.get("/bad")
        async def bad_route(request):
            return "not-a-response"

        try:
            response = await ASGIServer(app).handle_raw_request(build_get_request("/bad"))
        finally:
            await app.close()

        self.assertIn(b"HTTP/1.1 500 Internal Server Error", response)
        self.assertTrue(response.endswith(b"\r\n\r\nInternal Server Error"))


class ASGIServerProtocolErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_body_before_start_raises_clear_error(self) -> None:
        async def app(scope, receive, send) -> None:
            await receive()
            await send({"type": "http.response.body", "body": b"bad", "more_body": False})

        with self.assertRaisesRegex(ASGIServerError, "before http.response.start"):
            await ASGIServer(app).handle_raw_request(build_get_request("/bad-order"))

    async def test_duplicate_start_raises_clear_error(self) -> None:
        async def app(scope, receive, send) -> None:
            await receive()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.start", "status": 200, "headers": []})

        with self.assertRaisesRegex(ASGIServerError, "duplicate http.response.start"):
            await ASGIServer(app).handle_raw_request(build_get_request("/dup-start"))

    async def test_handler_that_never_finishes_response_raises_incomplete_error(self) -> None:
        async def app(scope, receive, send) -> None:
            await receive()
            await send({"type": "http.response.start", "status": 200, "headers": []})

        with self.assertRaisesRegex(ASGIServerError, "did not send a complete HTTP response"):
            await ASGIServer(app).handle_raw_request(build_get_request("/incomplete"))
