"""Tests for the tasgi framework core."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import sys
import threading
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from tasgi import (
    ASYNC_EXECUTION,
    APP_SCOPE,
    THREAD_EXECUTION,
    AppState,
    Depends,
    ExceptionMiddleware,
    JsonResponse,
    LoggingMiddleware,
    TasgiApp,
    TasgiConfig,
    StreamingResponse,
    TextResponse,
    TimingMiddleware,
)
from tasgi.asgi_server import ASGIServer
from tasgi.response import Response
from tasgi.routing import Router


def build_get_request(path: str) -> bytes:
    return f"GET {path} HTTP/1.1\r\nHost: example.test\r\n\r\n".encode("ascii")


def build_post_request(path: str, body: bytes) -> bytes:
    return (
        f"POST {path} HTTP/1.1\r\nHost: example.test\r\nContent-Length: {len(body)}\r\n\r\n".encode(
            "ascii"
        )
        + body
    )


class TasgiConfigTests(unittest.TestCase):
    def test_config_defaults(self) -> None:
        config = TasgiConfig()
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 8000)
        self.assertFalse(config.debug)
        self.assertEqual(config.default_execution, ASYNC_EXECUTION)
        self.assertEqual(config.max_request_body_size, 1_048_576)
        self.assertTrue(config.http2)
        self.assertEqual(config.title, "tasgi")
        self.assertEqual(config.version, "0.1.0")
        self.assertFalse(config.docs)
        self.assertIsNone(config.openapi_url)
        self.assertIsNone(config.docs_url)
        self.assertIsNone(config.tls_certfile)
        self.assertIsNone(config.tls_keyfile)

    def test_app_creation_exposes_config_and_state(self) -> None:
        config = TasgiConfig(default_execution=THREAD_EXECUTION, thread_pool_workers=8)
        app = TasgiApp(config=config)
        self.assertIs(app.config, config)
        self.assertIsInstance(app.state, AppState)
        self.assertEqual(app.config.thread_pool_workers, 8)

    def test_app_creation_accepts_config_keyword_overrides(self) -> None:
        app = TasgiApp(debug=True, default_execution=THREAD_EXECUTION, thread_pool_workers=4)
        self.assertTrue(app.config.debug)
        self.assertEqual(app.config.default_execution, THREAD_EXECUTION)
        self.assertEqual(app.config.thread_pool_workers, 4)

    def test_tls_cert_and_key_must_be_provided_together(self) -> None:
        with self.assertRaisesRegex(ValueError, "tls_certfile and tls_keyfile"):
            TasgiConfig(tls_certfile="cert.pem")

    def test_app_state_handles_concurrent_access(self) -> None:
        state = AppState()
        failures: list[Exception] = []
        start = threading.Event()

        def worker(index: int) -> None:
            try:
                start.wait()
                for iteration in range(50):
                    setattr(state, f"key_{index}_{iteration}", iteration)
                    self.assertEqual(getattr(state, f"key_{index}_{iteration}"), iteration)
            except Exception as exc:  # pragma: no cover - test helper path
                failures.append(exc)

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(4)]
        for thread in threads:
            thread.start()
        start.set()
        for thread in threads:
            thread.join()

        self.assertEqual(failures, [])
        snapshot = state.snapshot()
        self.assertEqual(len(snapshot), 200)

    def test_app_state_service_helpers_are_explicit(self) -> None:
        state = AppState()
        service = object()

        returned = state.set_service("cache", service)

        self.assertIs(returned, service)
        self.assertIs(state.get_service("cache"), service)
        self.assertIs(state.require_service("cache"), service)
        self.assertIs(state.remove_service("cache"), service)
        self.assertEqual(state.get_service("cache", "missing"), "missing")
        with self.assertRaisesRegex(KeyError, "cache"):
            state.require_service("cache")

    def test_tasgi_app_service_helpers_delegate_to_state(self) -> None:
        app = TasgiApp()
        service = object()

        app.add_service("db", service)

        self.assertIs(app.get_service("db"), service)
        self.assertIs(app.require_service("db"), service)
        self.assertIs(app.remove_service("db"), service)


class TasgiLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifecycle_hooks_register_and_run(self) -> None:
        app = TasgiApp()
        events: list[str] = []

        @app.on_startup
        def sync_startup(app_instance) -> None:
            app_instance.state.message = "tasgi ready"
            events.append("sync-startup")

        @app.on_startup
        async def async_startup(app_instance) -> None:
            events.append(app_instance.state.message)

        @app.on_shutdown
        def sync_shutdown(app_instance) -> None:
            events.append("sync-shutdown")

        @app.on_shutdown
        async def async_shutdown(app_instance) -> None:
            events.append("async-shutdown")

        await app.startup()
        await app.shutdown()

        self.assertEqual(
            events,
            ["sync-startup", "tasgi ready", "async-shutdown", "sync-shutdown"],
        )
        self.assertEqual(app.lifecycle_state, "stopped")
        self.assertFalse(app.runtime.started)
        self.assertTrue(app.runtime.closed)

    async def test_lifespan_context_initializes_and_cleans_stateful_services(self) -> None:
        app = TasgiApp()

        class Service:
            def __init__(self) -> None:
                self.closed = False

            def close(self) -> None:
                self.closed = True

        @app.on_startup
        def startup(app_instance) -> None:
            app_instance.state.service = Service()

        @app.on_shutdown
        def shutdown(app_instance) -> None:
            app_instance.state.service.close()
            del app_instance.state.service

        async with app.lifespan():
            self.assertTrue(app.started)
            self.assertEqual(app.lifecycle_state, "started")
            self.assertTrue(app.runtime.started)
            self.assertFalse(app.runtime.closed)
            self.assertFalse(app.state.service.closed)

        self.assertEqual(app.lifecycle_state, "stopped")
        self.assertFalse(app.runtime.started)
        self.assertTrue(app.runtime.closed)
        with self.assertRaises(AttributeError):
            _ = app.state.service

    async def test_startup_failure_closes_owned_runtime(self) -> None:
        app = TasgiApp()

        @app.on_startup
        def startup(app_instance) -> None:
            app_instance.state.message = "starting"
            raise RuntimeError("startup failed")

        with self.assertRaisesRegex(RuntimeError, "startup failed"):
            await app.startup()

        self.assertEqual(app.lifecycle_state, "failed")
        self.assertFalse(app.runtime.started)
        self.assertTrue(app.runtime.closed)


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

    async def test_get_post_404_and_405(self) -> None:
        app = TasgiApp()

        @app.get("/")
        async def home(request) -> TextResponse:
            return TextResponse("home")

        @app.post("/echo")
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

        @app.get("/users/{id}/posts/{post_id}", metadata={"name": "user-post-detail"})
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
        self.assertEqual(route.metadata, {"name": "user-post-detail"})


class TasgiDocsTests(unittest.IsolatedAsyncioTestCase):
    def test_openapi_schema_collects_route_metadata_and_registered_schemas(self) -> None:
        app = TasgiApp()
        app.configure_docs(title="Demo API", version="1.2.0", description="Demo docs")

        @app.get(
            "/users/{id}",
            summary="Get user",
            description="Return one user",
            tags=["users"],
            operation_id="getUser",
        )
        async def get_user(request) -> JsonResponse:
            return JsonResponse({"id": request.route_params["id"]})

        @app.post("/users", summary="Create user")
        def create_user(request) -> JsonResponse:
            return JsonResponse({"created": True}, status_code=201)

        @app.websocket("/ws")
        async def websocket_route(websocket) -> None:
            await websocket.accept()
            await websocket.close()

        app.register_request_schema(
            "/users",
            "POST",
            {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
            description="User create payload",
        )
        app.register_response_schema(
            "/users/{id}",
            "GET",
            200,
            {"type": "object", "properties": {"id": {"type": "string"}}},
            description="User payload",
        )
        app.register_response_schema(
            "/users",
            "POST",
            201,
            {"type": "object", "properties": {"created": {"type": "boolean"}}},
        )

        document = app.openapi_schema()

        self.assertEqual(document["openapi"], "3.1.0")
        self.assertEqual(document["info"]["title"], "Demo API")
        self.assertEqual(document["info"]["version"], "1.2.0")
        self.assertEqual(document["info"]["description"], "Demo docs")
        self.assertNotIn("/ws", document["paths"])

        get_operation = document["paths"]["/users/{id}"]["get"]
        self.assertEqual(get_operation["summary"], "Get user")
        self.assertEqual(get_operation["description"], "Return one user")
        self.assertEqual(get_operation["tags"], ["users"])
        self.assertEqual(get_operation["operationId"], "getUser")
        self.assertEqual(get_operation["parameters"][0]["name"], "id")
        self.assertEqual(get_operation["responses"]["200"]["description"], "User payload")
        self.assertEqual(
            get_operation["responses"]["200"]["content"]["application/json"]["schema"]["type"],
            "object",
        )

        post_operation = document["paths"]["/users"]["post"]
        self.assertEqual(post_operation["summary"], "Create user")
        self.assertTrue(post_operation["requestBody"]["required"])
        self.assertEqual(post_operation["requestBody"]["description"], "User create payload")
        self.assertEqual(
            post_operation["requestBody"]["content"]["application/json"]["schema"]["required"],
            ["name"],
        )
        self.assertEqual(post_operation["responses"]["201"]["description"], "HTTP 201 response")
        self.assertEqual(post_operation["x-tasgi-execution"], THREAD_EXECUTION)

    def test_openapi_schema_defaults_to_success_response_without_explicit_docs(self) -> None:
        app = TasgiApp()

        @app.get("/")
        async def home(request) -> TextResponse:
            return TextResponse("home")

        document = app.openapi_schema()
        self.assertEqual(document["paths"]["/"]["get"]["responses"], {"200": {"description": "Successful Response"}})

    async def test_builtin_openapi_and_docs_routes_work_from_config(self) -> None:
        app = TasgiApp(docs=True, title="Demo Docs", version="2.0.0")

        @app.get("/", summary="Home", response_model=str)
        async def home(request) -> str:
            return "home"

        try:
            openapi_response, docs_response = await asyncio.gather(
                ASGIServer(app).handle_raw_request(build_get_request("/openapi.json")),
                ASGIServer(app).handle_raw_request(build_get_request("/docs")),
            )
        finally:
            await app.close()

        self.assertIn(b'"title": "Demo Docs"', openapi_response)
        self.assertIn(b'"/": {"get":', openapi_response)
        self.assertIn(b"swagger-ui", docs_response)
        self.assertIn(b"/openapi.json", docs_response)

    def test_openapi_infers_request_and_response_models(self) -> None:
        @dataclass
        class EchoIn:
            message: str

        @dataclass
        class EchoOut:
            echoed: str

        app = TasgiApp()

        @app.post(
            "/echo",
            summary="Echo message",
            tags=["demo"],
            request_model=EchoIn,
            response_model=EchoOut,
            status_code=201,
        )
        def echo(request, body: EchoIn) -> EchoOut:
            return EchoOut(echoed=body.message)

        document = app.openapi_schema()
        operation = document["paths"]["/echo"]["post"]
        self.assertEqual(operation["summary"], "Echo message")
        self.assertEqual(operation["tags"], ["demo"])
        self.assertEqual(operation["requestBody"]["content"]["application/json"]["schema"]["required"], ["message"])
        self.assertEqual(
            operation["responses"]["201"]["content"]["application/json"]["schema"]["properties"]["echoed"]["type"],
            "string",
        )

    async def test_typed_body_parameter_and_model_return_are_coerced_automatically(self) -> None:
        @dataclass
        class EchoIn:
            message: str

        @dataclass
        class EchoOut:
            echoed: str

        app = TasgiApp()

        @app.post("/echo", request_model=EchoIn, response_model=EchoOut, status_code=201)
        def echo(request, body: EchoIn) -> EchoOut:
            self.assertIsInstance(body, EchoIn)
            return EchoOut(echoed=body.message)

        try:
            response = await ASGIServer(app).handle_raw_request(build_post_request("/echo", b'{"message":"hi"}'))
        finally:
            await app.close()

        self.assertIn(b"HTTP/1.1 201 Created", response)
        self.assertIn(b'{"echoed": "hi"}', response)

    async def test_exact_route_wins_before_param_route(self) -> None:
        app = TasgiApp()

        @app.get("/users/me")
        async def me(request) -> TextResponse:
            return TextResponse("exact")

        @app.get("/users/{id}")
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
            method_response = await ASGIServer(app).handle_raw_request(build_get_request("/api/users/42"))
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

    async def test_request_text_json_and_app_state(self) -> None:
        app = TasgiApp(config=TasgiConfig(debug=True))

        @app.on_startup
        def startup(app_instance) -> None:
            app_instance.state.message = "ready"

        @app.post("/inspect")
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
                .replace(b"\r\n\r\n", b"\r\nContent-Type: application/json\r\n\r\n", 1)
            )
        finally:
            await app.close()

        self.assertIn(b'"text": "{\\"a\\":1}"', response)
        self.assertIn(b'"a": 1', response)
        self.assertIn(b'"message": "ready"', response)
        self.assertIn(b'"content_type": "application/json"', response)
        self.assertIn(b'"query": ""', response)

    async def test_request_service_access_is_explicit_and_thread_safe(self) -> None:
        app = TasgiApp(config=TasgiConfig(default_execution=THREAD_EXECUTION))

        class CounterService:
            def __init__(self) -> None:
                self._lock = threading.Lock()
                self.value = 0

            def increment(self) -> int:
                with self._lock:
                    self.value += 1
                    return self.value

        @app.on_startup
        def startup(app_instance) -> None:
            app_instance.add_service("counter", CounterService())

        @app.on_shutdown
        def shutdown(app_instance) -> None:
            app_instance.remove_service("counter")

        @app.get("/count")
        def count(request) -> JsonResponse:
            counter = request.service("counter")
            missing = request.service("missing", "fallback")
            return JsonResponse({"value": counter.increment(), "missing": missing})

        try:
            responses = await asyncio.gather(
                *[ASGIServer(app).handle_raw_request(build_get_request("/count")) for _ in range(3)]
            )
        finally:
            await app.close()

        values: list[int] = []
        for response in responses:
            body = response.split(b"\r\n\r\n", maxsplit=1)[1]
            self.assertIn(b'"missing": "fallback"', body)
            if b'"value": 1' in body:
                values.append(1)
            elif b'"value": 2' in body:
                values.append(2)
            elif b'"value": 3' in body:
                values.append(3)

        self.assertEqual(sorted(values), [1, 2, 3])

    def test_response_serialization_emits_final_body_frame(self) -> None:
        response = TextResponse("hello", headers=[("x-demo", "1")])
        messages = response.to_asgi_messages()
        self.assertEqual(messages[0]["type"], "http.response.start")
        self.assertEqual(messages[1]["type"], "http.response.body")
        self.assertEqual(messages[1]["body"], b"hello")
        self.assertFalse(messages[1]["more_body"])


class TasgiExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_scoped_dependencies_are_cached_per_request(self) -> None:
        app = TasgiApp()
        calls = {"token": 0}

        def get_token(request) -> str:
            calls["token"] += 1
            return "token:%s" % request.path

        def build_message(token=Depends(get_token)) -> str:
            return "message:%s" % token

        @app.get("/deps")
        async def deps_route(
            request,
            token=Depends(get_token),
            message=Depends(build_message),
        ) -> JsonResponse:
            return JsonResponse({"token": token, "message": message})

        try:
            response = await ASGIServer(app).handle_raw_request(build_get_request("/deps"))
        finally:
            await app.close()

        self.assertIn(b'"token": "token:/deps"', response)
        self.assertIn(b'"message": "message:token:/deps"', response)
        self.assertEqual(calls["token"], 1)

    async def test_app_scoped_dependencies_are_cached_across_requests(self) -> None:
        app = TasgiApp()
        calls = {"settings": 0}

        def get_settings(app) -> dict[str, str]:
            calls["settings"] += 1
            return {"prefix": "cached"}

        @app.get("/cache")
        async def cache_route(request, settings=Depends(get_settings, scope=APP_SCOPE)) -> TextResponse:
            return TextResponse(settings["prefix"])

        try:
            responses = await asyncio.gather(
                ASGIServer(app).handle_raw_request(build_get_request("/cache")),
                ASGIServer(app).handle_raw_request(build_get_request("/cache")),
            )
        finally:
            await app.close()

        self.assertEqual(calls["settings"], 1)
        self.assertTrue(all(response.endswith(b"\r\n\r\ncached") for response in responses))

    async def test_dependencies_resolve_across_async_and_thread_modes(self) -> None:
        app = TasgiApp(config=TasgiConfig(default_execution=THREAD_EXECUTION))
        loop_thread_id = threading.get_ident()
        sync_dependency_threads: list[int] = []
        async_dependency_threads: list[int] = []
        sync_handler_threads: list[int] = []

        def sync_dependency(request) -> str:
            sync_dependency_threads.append(threading.get_ident())
            return request.path

        async def async_dependency(request) -> str:
            async_dependency_threads.append(threading.get_ident())
            return request.path.upper()

        @app.get("/async", execution=ASYNC_EXECUTION)
        async def async_route(request, path=Depends(sync_dependency)) -> TextResponse:
            return TextResponse("async:%s" % path)

        @app.get("/thread")
        def thread_route(request, upper=Depends(async_dependency)) -> TextResponse:
            sync_handler_threads.append(threading.get_ident())
            return TextResponse("thread:%s" % upper)

        try:
            async_response, thread_response = await asyncio.gather(
                ASGIServer(app).handle_raw_request(build_get_request("/async")),
                ASGIServer(app).handle_raw_request(build_get_request("/thread")),
            )
        finally:
            await app.close()

        self.assertIn(b"async:/async", async_response)
        self.assertIn(b"thread:/THREAD", thread_response)
        self.assertEqual(async_dependency_threads, [loop_thread_id])
        self.assertTrue(all(thread_id != loop_thread_id for thread_id in sync_dependency_threads))
        self.assertEqual(len(sync_handler_threads), 1)
        self.assertNotEqual(sync_handler_threads[0], loop_thread_id)

    async def test_app_scoped_dependency_cannot_depend_on_request_object(self) -> None:
        app = TasgiApp(debug=True)

        def invalid_dependency(request) -> str:
            return request.path

        @app.get("/bad")
        async def bad_route(request, value=Depends(invalid_dependency, scope=APP_SCOPE)) -> TextResponse:
            return TextResponse(value)

        try:
            response = await ASGIServer(app).handle_raw_request(build_get_request("/bad"))
        finally:
            await app.close()

        self.assertIn(
            b"App-scoped dependencies cannot depend on the request object.",
            response,
        )

    async def test_async_streaming_response_is_sent_in_multiple_body_messages(self) -> None:
        app = TasgiApp()

        @app.get("/stream")
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
        app = TasgiApp(config=TasgiConfig(default_execution=THREAD_EXECUTION))
        loop_thread_id = threading.get_ident()
        generator_thread_ids: list[int] = []

        @app.get("/stream")
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

    async def test_async_handlers_run_on_event_loop_and_sync_handlers_run_in_threads(self) -> None:
        app = TasgiApp(config=TasgiConfig(default_execution=ASYNC_EXECUTION))
        loop_thread_id = threading.get_ident()
        seen_async_threads: list[int] = []
        seen_sync_threads: list[int] = []

        @app.get("/json")
        async def json_route(request) -> JsonResponse:
            seen_async_threads.append(threading.get_ident())
            return JsonResponse({"ok": True})

        @app.post("/echo")
        def echo_route(request) -> TextResponse:
            seen_sync_threads.append(threading.get_ident())
            return TextResponse(request.text())

        try:
            json_response, echo_response = await asyncio.gather(
                ASGIServer(app).handle_raw_request(build_get_request("/json")),
                ASGIServer(app).handle_raw_request(build_post_request("/echo", b'{"a":1}')),
            )
        finally:
            await app.close()

        self.assertIn(b'{"ok": true}', json_response)
        self.assertTrue(echo_response.endswith(b'\r\n\r\n{"a":1}'))
        self.assertEqual(seen_async_threads, [loop_thread_id])
        self.assertEqual(len(seen_sync_threads), 1)
        self.assertNotEqual(seen_sync_threads[0], loop_thread_id)

    async def test_route_level_execution_override_in_thread_default_app(self) -> None:
        app = TasgiApp(config=TasgiConfig(default_execution=THREAD_EXECUTION))
        loop_thread_id = threading.get_ident()
        seen_async_threads: list[int] = []
        seen_sync_threads: list[int] = []

        @app.get("/sync")
        def sync_route(request) -> TextResponse:
            seen_sync_threads.append(threading.get_ident())
            return TextResponse("sync route")

        @app.get("/async", execution=ASYNC_EXECUTION)
        async def async_route(request) -> TextResponse:
            seen_async_threads.append(threading.get_ident())
            return TextResponse("async route")

        try:
            sync_response, async_response = await asyncio.gather(
                ASGIServer(app).handle_raw_request(build_get_request("/sync")),
                ASGIServer(app).handle_raw_request(build_get_request("/async")),
            )
        finally:
            await app.close()

        self.assertIn(b"sync route", sync_response)
        self.assertIn(b"async route", async_response)
        self.assertEqual(len(seen_sync_threads), 1)
        self.assertNotEqual(seen_sync_threads[0], loop_thread_id)
        self.assertEqual(seen_async_threads, [loop_thread_id])

    async def test_async_route_works_without_override_in_thread_default_app(self) -> None:
        app = TasgiApp(config=TasgiConfig(default_execution=THREAD_EXECUTION))

        @app.get("/async")
        async def async_route(request) -> TextResponse:
            return TextResponse("async default")

        try:
            response = await ASGIServer(app).handle_raw_request(build_get_request("/async"))
        finally:
            await app.close()

        self.assertIn(b"async default", response)

    async def test_multiple_concurrent_sync_requests_do_not_corrupt_state(self) -> None:
        app = TasgiApp(config=TasgiConfig(default_execution=THREAD_EXECUTION))

        @app.post("/echo")
        def echo_route(request) -> TextResponse:
            return TextResponse(request.text())

        payloads = [f"payload-{index}".encode("ascii") for index in range(5)]
        try:
            responses = await asyncio.gather(
                *[
                    ASGIServer(app).handle_raw_request(build_post_request("/echo", payload))
                    for payload in payloads
                ]
            )
        finally:
            await app.close()

        bodies = [response.split(b"\r\n\r\n", maxsplit=1)[1] for response in responses]
        self.assertEqual(bodies, payloads)

    async def test_transport_writes_stay_on_event_loop_for_sync_handlers(self) -> None:
        from tasgi.asgi_server import BufferingTransport

        app = TasgiApp(config=TasgiConfig(default_execution=THREAD_EXECUTION))
        loop_thread_id = threading.get_ident()
        handler_thread_ids: list[int] = []
        transport = BufferingTransport()

        @app.get("/thread")
        def thread_route(request) -> TextResponse:
            handler_thread_ids.append(threading.get_ident())
            return TextResponse("thread route")

        try:
            await ASGIServer(app).handle_raw_request(
                build_get_request("/thread"),
                transport=transport,
            )
        finally:
            await app.close()

        self.assertEqual(len(handler_thread_ids), 1)
        self.assertNotEqual(handler_thread_ids[0], loop_thread_id)
        self.assertEqual(transport.write_thread_ids, [loop_thread_id])

    async def test_cpu_handler_returns_expected_result(self) -> None:
        app = TasgiApp(config=TasgiConfig(default_execution=THREAD_EXECUTION))

        @app.get("/cpu")
        def cpu_route(request) -> TextResponse:
            return TextResponse("CPU result: %s" % cpu_demo_work())

        try:
            response = await ASGIServer(app).handle_raw_request(build_get_request("/cpu"))
        finally:
            await app.close()

        expected = f"CPU result: {cpu_demo_work()}".encode("utf-8")
        self.assertTrue(response.endswith(b"\r\n\r\n" + expected))


class TasgiMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_middleware_chain_wraps_async_and_sync_handlers(self) -> None:
        app = TasgiApp(default_execution=THREAD_EXECUTION)
        events: list[str] = []

        @app.middleware
        async def record(request, call_next):
            events.append("before:%s" % request.path)
            response = await call_next(request)
            events.append("after:%s:%s" % (request.path, response.status_code))
            return response

        @app.get("/async")
        async def async_route(request) -> TextResponse:
            events.append("handler:async")
            return TextResponse("async")

        @app.get("/sync")
        def sync_route(request) -> TextResponse:
            events.append("handler:sync")
            return TextResponse("sync")

        try:
            async_response, sync_response = await asyncio.gather(
                ASGIServer(app).handle_raw_request(build_get_request("/async")),
                ASGIServer(app).handle_raw_request(build_get_request("/sync")),
            )
        finally:
            await app.close()

        self.assertIn(b"async", async_response)
        self.assertIn(b"sync", sync_response)
        self.assertIn("before:/async", events)
        self.assertIn("after:/async:200", events)
        self.assertIn("before:/sync", events)
        self.assertIn("after:/sync:200", events)

    async def test_timing_middleware_adds_response_header(self) -> None:
        app = TasgiApp()
        app.add_middleware(TimingMiddleware())

        @app.get("/")
        async def home(request) -> TextResponse:
            return TextResponse("home")

        try:
            response = await ASGIServer(app).handle_raw_request(build_get_request("/"))
        finally:
            await app.close()

        self.assertIn(b"x-process-time: ", response)

    async def test_logging_middleware_records_request_lifecycle(self) -> None:
        logs: list[str] = []
        app = TasgiApp()
        app.add_middleware(LoggingMiddleware(logger=logs.append))

        @app.get("/")
        async def home(request) -> TextResponse:
            return TextResponse("home")

        try:
            response = await ASGIServer(app).handle_raw_request(build_get_request("/"))
        finally:
            await app.close()

        self.assertIn(b"home", response)
        self.assertEqual(len(logs), 2)
        self.assertIn("tasgi request started: GET /", logs[0])
        self.assertIn("tasgi request completed: GET / -> 200", logs[1])

    async def test_exception_middleware_wraps_handler_errors(self) -> None:
        app = TasgiApp(debug=True)
        app.add_middleware(ExceptionMiddleware())

        @app.get("/error")
        async def error_route(request):
            raise RuntimeError("wrapped")

        try:
            response = await ASGIServer(app).handle_raw_request(build_get_request("/error"))
        finally:
            await app.close()

        self.assertIn(b"Internal Server Error: RuntimeError: wrapped on GET /error", response)


class TasgiErrorHandlingTests(unittest.IsolatedAsyncioTestCase):
    async def test_handler_exception_returns_generic_500_in_production_mode(self) -> None:
        app = TasgiApp(config=TasgiConfig(debug=False))

        @app.get("/error")
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

        @app.get("/error")
        def error_route(request):
            raise RuntimeError("boom")

        try:
            response = await ASGIServer(app).handle_raw_request(build_get_request("/error"))
        finally:
            await app.close()

        self.assertIn(b"Internal Server Error: RuntimeError: boom on GET /error", response)

    async def test_invalid_handler_return_still_produces_complete_500_response(self) -> None:
        app = TasgiApp(config=TasgiConfig(debug=False))

        @app.get("/bad")
        async def bad_route(request):
            return "not-a-response"

        try:
            response = await ASGIServer(app).handle_raw_request(build_get_request("/bad"))
        finally:
            await app.close()

        self.assertIn(b"HTTP/1.1 500 Internal Server Error", response)
        self.assertTrue(response.endswith(b"\r\n\r\nInternal Server Error"))


if __name__ == "__main__":
    unittest.main()


def cpu_demo_work(iterations: int = 60_000) -> int:
    """Mirror the deterministic CPU demo workload used in framework tests."""

    total = 0
    for index in range(iterations):
        total += (index * index) % 97
    return total
