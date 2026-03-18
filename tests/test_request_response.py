"""Request/response object tests."""

from __future__ import annotations

import asyncio
import unittest

from support import build_get_request, build_post_request
from tasgi import JsonResponse, TasgiApp, TextResponse
from tasgi.auth.models import AuthContext, Identity
from tasgi.asgi_server import ASGIServer
from tasgi.request import Request
from tasgi.response import Response, StreamingResponse


class RequestTests(unittest.IsolatedAsyncioTestCase):
    def test_request_from_scope_and_auth_aliases(self) -> None:
        app = TasgiApp()
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/items",
            "query_string": b"page=1",
            "headers": [(b"content-type", b"application/json"), (b"x-demo", b"1")],
            "http_version": "2",
        }
        request = Request.from_scope(app, scope, b'{"a":1}', {"id": "42"})
        authed = request.with_auth(
            AuthContext(
                identity=Identity(subject="alice"),
                backend="bearer",
            )
        )

        self.assertEqual(request.method, "POST")
        self.assertEqual(request.path, "/items")
        self.assertEqual(request.query, "page=1")
        self.assertEqual(request.json(), {"a": 1})
        self.assertEqual(request.header("x-demo"), "1")
        self.assertEqual(request.route_params, {"id": "42"})
        self.assertEqual(authed.identity.subject, "alice")
        self.assertEqual(authed.user.subject, "alice")

    async def test_request_text_json_and_app_state(self) -> None:
        app = TasgiApp(debug=True)

        @app.on_startup
        def startup(app_instance) -> None:
            app_instance.state.message = "ready"

        @app.route.post("/inspect")
        async def inspect_request(request) -> JsonResponse:
            return JsonResponse(
                {
                    "text": request.text(),
                    "json": request.json(),
                    "query": request.query,
                    "message": request.app.state.message,
                    "content_type": request.header("content-type"),
                }
            )

        try:
            response = await ASGIServer(app).handle_raw_request(
                build_post_request("/inspect", b'{"a":1}')
            )
        finally:
            await app.close()

        self.assertIn(b'"text": "{\\"a\\":1}"', response)
        self.assertIn(b'"a": 1', response)
        self.assertIn(b'"message": "ready"', response)
        self.assertIn(b'"content_type": "application/json"', response)
        self.assertIn(b'"query": ""', response)


class ResponseTests(unittest.IsolatedAsyncioTestCase):
    def test_response_serialization_emits_final_body_frame(self) -> None:
        response = TextResponse("hello", headers=[("x-demo", "1")])
        messages = response.to_asgi_messages()
        self.assertEqual(messages[0]["type"], "http.response.start")
        self.assertEqual(messages[1]["type"], "http.response.body")
        self.assertEqual(messages[1]["body"], b"hello")
        self.assertFalse(messages[1]["more_body"])

    def test_response_text_and_json_helpers_encode_bytes(self) -> None:
        text_response = Response.text("hello", status_code=201)
        json_response = Response.json({"ok": True})

        self.assertEqual(text_response.status, 201)
        self.assertEqual(text_response.body, b"hello")
        self.assertIn((b"content-type", b"text/plain; charset=utf-8"), text_response.headers)
        self.assertEqual(json_response.body, b'{"ok": true}')
        self.assertIn((b"content-type", b"application/json"), json_response.headers)

    async def test_streaming_response_iter_messages_finish_with_final_body(self) -> None:
        async def collect_messages():
            response = StreamingResponse([b"one", "two"])
            return [message async for message in response.iter_asgi_messages()]

        messages = await collect_messages()
        self.assertEqual(messages[0]["type"], "http.response.start")
        self.assertEqual(messages[1]["body"], b"one")
        self.assertTrue(messages[1]["more_body"])
        self.assertEqual(messages[2]["body"], b"two")
        self.assertTrue(messages[2]["more_body"])
        self.assertEqual(messages[3]["body"], b"")
        self.assertFalse(messages[3]["more_body"])

    async def test_streaming_response_rejects_invalid_chunk_type(self) -> None:
        response = StreamingResponse([123])  # type: ignore[list-item]

        with self.assertRaisesRegex(TypeError, "Streaming response chunks must be bytes or text"):
            async for _ in response.iter_asgi_messages():
                pass

    async def test_empty_response_body_serializes_cleanly(self) -> None:
        app = TasgiApp()

        @app.route.get("/empty")
        async def empty(request) -> Response:
            return Response(status_code=204)

        try:
            response = await ASGIServer(app).handle_raw_request(build_get_request("/empty"))
        finally:
            await app.close()

        self.assertIn(b"HTTP/1.1 204 No Content", response)
        self.assertTrue(response.endswith(b"\r\n\r\n"))
