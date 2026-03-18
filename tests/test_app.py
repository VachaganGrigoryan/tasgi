"""App/config/lifecycle and example application tests."""

from __future__ import annotations

import asyncio
import threading
import unittest

from support import (
    MODULAR_API_ROOT,
    SERVICE_API_ROOT,
    build_get_request,
    build_post_request,
    load_example_module,
    with_header,
)
from tasgi import (
    AppState,
    ExceptionMiddleware,
    LoggingMiddleware,
    TasgiApp,
    TasgiConfig,
    TextResponse,
    THREAD_EXECUTION,
    TimingMiddleware,
)
from tasgi.asgi_server import ASGIServer
from tasgi.main import _load_repo_service_app


class TasgiConfigTests(unittest.TestCase):
    def test_config_defaults(self) -> None:
        config = TasgiConfig()
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 8000)
        self.assertFalse(config.debug)
        self.assertEqual(config.default_execution, "async")
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
            except Exception as exc:  # pragma: no cover
                failures.append(exc)

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(4)]
        for thread in threads:
            thread.start()
        start.set()
        for thread in threads:
            thread.join()

        self.assertEqual(failures, [])
        self.assertEqual(len(state.snapshot()), 200)

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

        @app.route.get("/async")
        async def async_route(request) -> TextResponse:
            events.append("handler:async")
            return TextResponse("async")

        @app.route.get("/sync")
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

        @app.route.get("/")
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

        @app.route.get("/")
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

        @app.route.get("/error")
        async def error_route(request):
            raise RuntimeError("wrapped")

        try:
            response = await ASGIServer(app).handle_raw_request(build_get_request("/error"))
        finally:
            await app.close()

        self.assertIn(b"Internal Server Error: RuntimeError: wrapped on GET /error", response)


class ServiceAPIExampleTests(unittest.IsolatedAsyncioTestCase):
    async def test_overview_and_catalog_routes(self) -> None:
        app = load_example_module("service_api_example", SERVICE_API_ROOT).build_app()

        try:
            overview_response = await ASGIServer(app).handle_raw_request(build_get_request("/"))
            catalog_response = await ASGIServer(app).handle_raw_request(
                build_get_request("/api/catalog/products")
            )
        finally:
            await app.close()

        self.assertIn(b'"service": "tasgi service api"', overview_response)
        self.assertIn(b'"sku": "sku-laptop-14"', catalog_response)

    async def test_authenticated_order_flow(self) -> None:
        app = load_example_module("service_api_example_orders", SERVICE_API_ROOT).build_app()

        try:
            create_response = await ASGIServer(app).handle_raw_request(
                with_header(
                    build_post_request(
                        "/api/orders",
                        b'{"items":[{"sku":"sku-laptop-14","quantity":1}]}',
                    ),
                    "Authorization",
                    "Bearer demo-token",
                )
            )
            list_response = await ASGIServer(app).handle_raw_request(
                with_header(build_get_request("/api/orders"), "Authorization", "Bearer demo-token")
            )
        finally:
            await app.close()

        self.assertIn(b"HTTP/1.1 201 Created", create_response)
        self.assertIn(b'"customer_id": "alice"', create_response)
        self.assertIn(b'"order_id": "ord-', list_response)

    async def test_ops_routes_enforce_auth_and_scope(self) -> None:
        app = load_example_module("service_api_example_ops", SERVICE_API_ROOT).build_app()

        try:
            unauthorized_metrics = await ASGIServer(app).handle_raw_request(
                build_get_request("/api/ops/metrics")
            )
            ops_metrics = await ASGIServer(app).handle_raw_request(
                with_header(
                    build_get_request("/api/ops/metrics"),
                    "Authorization",
                    "Bearer ops-token",
                )
            )
            forbidden_job = await ASGIServer(app).handle_raw_request(
                with_header(
                    build_post_request("/api/ops/jobs/rebuild-search-index", b"{}"),
                    "Authorization",
                    "Bearer ops-token",
                )
            )
            admin_job = await ASGIServer(app).handle_raw_request(
                with_header(
                    build_post_request("/api/ops/jobs/rebuild-search-index", b"{}"),
                    "Authorization",
                    "Bearer admin-token",
                )
            )
        finally:
            await app.close()

        self.assertIn(b"HTTP/1.1 401 Unauthorized", unauthorized_metrics)
        self.assertIn(b'"total_orders":', ops_metrics)
        self.assertIn(b"HTTP/1.1 403 Forbidden", forbidden_job)
        self.assertIn(b'"job": "search-index"', admin_job)


class ModularAPIExampleTests(unittest.IsolatedAsyncioTestCase):
    async def test_modular_app_public_and_task_routes(self) -> None:
        app = load_example_module("modular_api_example", MODULAR_API_ROOT).build_app()

        try:
            public_response = await ASGIServer(app).handle_raw_request(build_get_request("/"))
            list_response = await ASGIServer(app).handle_raw_request(
                with_header(build_get_request("/api/tasks"), "Authorization", "Bearer demo-token")
            )
            create_response = await ASGIServer(app).handle_raw_request(
                with_header(
                    build_post_request("/api/tasks", b'{"title":"Ship weekly report","owner":"ops"}'),
                    "Authorization",
                    "Bearer writer-token",
                )
            )
        finally:
            await app.close()

        self.assertIn(b'"service": "tasgi modular api"', public_response)
        self.assertIn(b'"task_id": "task-001"', list_response)
        self.assertIn(b"HTTP/1.1 201 Created", create_response)
        self.assertIn(b'"title": "Ship weekly report"', create_response)


class ExampleLoaderTests(unittest.TestCase):
    def test_repo_service_example_loader_returns_app(self) -> None:
        app = _load_repo_service_app()
        self.assertIsNotNone(app)
        self.assertTrue(hasattr(app, "config"))
