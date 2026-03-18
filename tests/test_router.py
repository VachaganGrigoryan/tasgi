"""Routing-focused tests."""

from __future__ import annotations

import asyncio
import threading
import unittest

from support import build_get_request, build_post_request
from tasgi import ASYNC_EXECUTION, JsonResponse, TasgiApp, TasgiConfig, TextResponse, THREAD_EXECUTION
from tasgi.asgi_server import ASGIServer
from tasgi.routing import Router


class TasgiRoutingAndRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_route_decorator_defaults_to_get(self) -> None:
        app = TasgiApp()

        @app.route("/default")
        async def default_route(request) -> TextResponse:
            return TextResponse("default get")

        try:
            response = await ASGIServer(app).handle_raw_request(build_get_request("/default"))
        finally:
            await app.close()

        self.assertIn(b"default get", response)

    async def test_route_namespace_exposes_router_style_registration(self) -> None:
        app = TasgiApp()

        @app.route.get("/users")
        async def list_users(request) -> TextResponse:
            return TextResponse("users")

        @app.route.post("/users")
        def create_user(request) -> TextResponse:
            return TextResponse("created")

        try:
            get_response, post_response = await asyncio.gather(
                ASGIServer(app).handle_raw_request(build_get_request("/users")),
                ASGIServer(app).handle_raw_request(build_post_request("/users", b"body")),
            )
        finally:
            await app.close()

        self.assertTrue(get_response.endswith(b"\r\n\r\nusers"))
        self.assertTrue(post_response.endswith(b"\r\n\r\ncreated"))

    async def test_get_post_404_and_405(self) -> None:
        app = TasgiApp()

        @app.route.get("/")
        async def home(request) -> TextResponse:
            return TextResponse("home")

        @app.route.post("/echo")
        def echo(request) -> TextResponse:
            return TextResponse(request.text())

        try:
            home_response = await ASGIServer(app).handle_raw_request(build_get_request("/"))
            post_response = await ASGIServer(app).handle_raw_request(
                build_post_request("/echo", b"body")
            )
            missing_response = await ASGIServer(app).handle_raw_request(
                build_get_request("/missing")
            )
            method_response = await ASGIServer(app).handle_raw_request(
                build_get_request("/echo")
            )
        finally:
            await app.close()

        self.assertIn(b"HTTP/1.1 200 OK", home_response)
        self.assertTrue(post_response.endswith(b"\r\n\r\nbody"))
        self.assertIn(b"HTTP/1.1 404 Not Found", missing_response)
        self.assertIn(b"HTTP/1.1 405 Method Not Allowed", method_response)
        self.assertIn(b"allow: POST\r\n", method_response)

    async def test_path_params_are_exposed_to_handlers(self) -> None:
        app = TasgiApp()

        @app.route.get("/users/{id}/posts/{post_id}", metadata={"name": "user-post-detail"})
        async def post_detail(request) -> JsonResponse:
            return JsonResponse(
                {
                    "id": request.route_params["id"],
                    "post_id": request.route_params["post_id"],
                }
            )

        try:
            response = await ASGIServer(app).handle_raw_request(
                build_get_request("/users/42/posts/abc")
            )
        finally:
            await app.close()

        self.assertIn(b'"id": "42"', response)
        self.assertIn(b'"post_id": "abc"', response)
        route = app.router.resolve("GET", "/users/42/posts/abc").route
        self.assertIsNotNone(route)
        self.assertEqual(route.metadata["name"], "user-post-detail")


class TasgiRouterModuleTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_route_wins_before_param_route(self) -> None:
        app = TasgiApp()

        @app.route.get("/users/me")
        async def me(request) -> TextResponse:
            return TextResponse("exact")

        @app.route.get("/users/{id}")
        async def user_detail(request) -> TextResponse:
            return TextResponse("param:%s" % request.route_params["id"])

        try:
            exact_response, param_response = await asyncio.gather(
                ASGIServer(app).handle_raw_request(build_get_request("/users/me")),
                ASGIServer(app).handle_raw_request(build_get_request("/users/42")),
            )
        finally:
            await app.close()

        self.assertTrue(exact_response.endswith(b"\r\n\r\nexact"))
        self.assertTrue(param_response.endswith(b"\r\n\r\nparam:42"))

    def test_router_returns_sorted_allowed_methods_for_param_routes(self) -> None:
        router = Router()

        def handler(request) -> TextResponse:
            return TextResponse("ok")

        router.add_route("/items/{id}", ["POST", "GET"], handler)

        match = router.resolve("DELETE", "/items/1")

        self.assertIsNone(match.route)
        self.assertEqual(match.allowed_methods, ["GET", "POST"])

    def test_router_rejects_ambiguous_param_patterns(self) -> None:
        router = Router()

        def first(request) -> TextResponse:
            return TextResponse("first")

        def second(request) -> TextResponse:
            return TextResponse("second")

        router.add_route("/users/{id}", ["GET"], first)

        with self.assertRaisesRegex(ValueError, "Ambiguous parameter route"):
            router.add_route("/users/{name}", ["POST"], second)

    def test_router_rejects_invalid_path_param_syntax(self) -> None:
        router = Router()

        def handler(request) -> TextResponse:
            return TextResponse("ok")

        with self.assertRaisesRegex(ValueError, "Invalid path parameter segment"):
            router.add_route("/users/{id", ["GET"], handler)

    def test_router_decorators_register_http_methods(self) -> None:
        router = Router()

        @router.get("/users")
        def list_users(request) -> TextResponse:
            return TextResponse("list")

        @router.post("/users")
        def create_user(request) -> TextResponse:
            return TextResponse("create")

        @router.put("/users/{id}")
        def update_user(request) -> TextResponse:
            return TextResponse("update")

        @router.delete("/users/{id}")
        def delete_user(request) -> TextResponse:
            return TextResponse("delete")

        self.assertEqual(router.resolve("GET", "/users").route.handler, list_users)
        self.assertEqual(router.resolve("POST", "/users").route.handler, create_user)
        self.assertEqual(router.resolve("PUT", "/users/1").route.handler, update_user)
        self.assertEqual(router.resolve("DELETE", "/users/1").route.handler, delete_user)

    async def test_include_router_imports_routes_with_prefix(self) -> None:
        app = TasgiApp()
        router = Router()

        @router.get("/users")
        async def list_users(request) -> TextResponse:
            return TextResponse("users")

        @router.post("/users/{id}")
        def update_user(request) -> TextResponse:
            return TextResponse("user:%s" % request.route_params["id"])

        app.include_router(router, prefix="/api")

        try:
            get_response = await ASGIServer(app).handle_raw_request(build_get_request("/api/users"))
            post_response = await ASGIServer(app).handle_raw_request(
                build_post_request("/api/users/42", b"ignored")
            )
            method_response = await ASGIServer(app).handle_raw_request(
                build_get_request("/api/users/42")
            )
        finally:
            await app.close()

        self.assertTrue(get_response.endswith(b"\r\n\r\nusers"))
        self.assertTrue(post_response.endswith(b"\r\n\r\nuser:42"))
        self.assertIn(b"HTTP/1.1 405 Method Not Allowed", method_response)
        self.assertIn(b"allow: POST\r\n", method_response)

    async def test_include_router_preserves_execution_policy(self) -> None:
        app = TasgiApp(config=TasgiConfig(default_execution=THREAD_EXECUTION))
        router = Router()
        loop_thread_id = threading.get_ident()
        async_threads: list[int] = []
        sync_threads: list[int] = []

        @router.get("/async", execution=ASYNC_EXECUTION)
        async def async_route(request) -> TextResponse:
            async_threads.append(threading.get_ident())
            return TextResponse("async")

        @router.get("/sync")
        def sync_route(request) -> TextResponse:
            sync_threads.append(threading.get_ident())
            return TextResponse("sync")

        app.include_router(router, prefix="/module")

        try:
            async_response, sync_response = await asyncio.gather(
                ASGIServer(app).handle_raw_request(build_get_request("/module/async")),
                ASGIServer(app).handle_raw_request(build_get_request("/module/sync")),
            )
        finally:
            await app.close()

        self.assertIn(b"async", async_response)
        self.assertIn(b"sync", sync_response)
        self.assertEqual(async_threads, [loop_thread_id])
        self.assertEqual(len(sync_threads), 1)
        self.assertNotEqual(sync_threads[0], loop_thread_id)

    def test_include_router_rejects_invalid_prefix_and_conflicts(self) -> None:
        app = TasgiApp()
        router = Router()

        @router.get("/users")
        async def list_users(request) -> TextResponse:
            return TextResponse("users")

        with self.assertRaisesRegex(ValueError, "Router prefix must be empty or start with '/'"):
            app.include_router(router, prefix="api")

        app.include_router(router, prefix="/api")
        with self.assertRaisesRegex(ValueError, "Route already registered"):
            app.include_router(router, prefix="/api")
