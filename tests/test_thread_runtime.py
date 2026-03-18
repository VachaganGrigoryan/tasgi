"""Thread runtime and execution policy tests."""

from __future__ import annotations

import asyncio
import threading
import unittest

from support import build_get_request, build_post_request, cpu_demo_work
from tasgi import (
    APP_SCOPE,
    ASYNC_EXECUTION,
    Depends,
    JsonResponse,
    StreamingResponse,
    TasgiApp,
    TasgiConfig,
    TextResponse,
    THREAD_EXECUTION,
)
from tasgi.asgi_server import ASGIServer, BufferingTransport
from tasgi.runtime import TasgiRuntime, validate_execution_policy


class TasgiRuntimeDirectTests(unittest.IsolatedAsyncioTestCase):
    def test_validate_execution_policy_rejects_invalid_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "Execution policy must be 'async' or 'thread'"):
            validate_execution_policy("invalid")

    async def test_runtime_uses_cpu_pool_when_requested(self) -> None:
        runtime = TasgiRuntime(thread_pool_workers=1, cpu_thread_pool_workers=1)

        try:
            default_name = await runtime.run_sync(lambda: threading.current_thread().name)
            cpu_name = await runtime.run_sync(
                lambda: threading.current_thread().name,
                use_cpu_pool=True,
            )
        finally:
            await runtime.close()

        self.assertIn("tasgi-worker", default_name)
        self.assertIn("tasgi-cpu-worker", cpu_name)
        self.assertFalse(runtime.started)
        self.assertTrue(runtime.closed)

    async def test_runtime_shutdown_is_safe_before_startup(self) -> None:
        runtime = TasgiRuntime()
        await runtime.shutdown()
        self.assertFalse(runtime.started)
        self.assertTrue(runtime.closed)


class TasgiExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_scoped_dependencies_are_cached_per_request(self) -> None:
        app = TasgiApp()
        calls = {"token": 0}

        def get_token(request) -> str:
            calls["token"] += 1
            return "token:%s" % request.path

        def build_message(token=Depends(get_token)) -> str:
            return "message:%s" % token

        @app.route.get("/deps")
        async def deps_route(request, token=Depends(get_token), message=Depends(build_message)) -> JsonResponse:
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

        @app.route.get("/cache")
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

        @app.route.get("/async", execution=ASYNC_EXECUTION)
        async def async_route(request, path=Depends(sync_dependency)) -> TextResponse:
            return TextResponse("async:%s" % path)

        @app.route.get("/thread")
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

        @app.route.get("/bad")
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

    async def test_async_handlers_run_on_event_loop_and_sync_handlers_run_in_threads(self) -> None:
        app = TasgiApp(config=TasgiConfig(default_execution=ASYNC_EXECUTION))
        loop_thread_id = threading.get_ident()
        seen_async_threads: list[int] = []
        seen_sync_threads: list[int] = []

        @app.route.get("/json")
        async def json_route(request) -> JsonResponse:
            seen_async_threads.append(threading.get_ident())
            return JsonResponse({"ok": True})

        @app.route.post("/echo")
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

        @app.route.get("/sync")
        def sync_route(request) -> TextResponse:
            seen_sync_threads.append(threading.get_ident())
            return TextResponse("sync route")

        @app.route.get("/async", execution=ASYNC_EXECUTION)
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

        @app.route.get("/async")
        async def async_route(request) -> TextResponse:
            return TextResponse("async default")

        try:
            response = await ASGIServer(app).handle_raw_request(build_get_request("/async"))
        finally:
            await app.close()

        self.assertIn(b"async default", response)

    async def test_multiple_concurrent_sync_requests_do_not_corrupt_state(self) -> None:
        app = TasgiApp(config=TasgiConfig(default_execution=THREAD_EXECUTION))

        @app.route.post("/echo")
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
        app = TasgiApp(config=TasgiConfig(default_execution=THREAD_EXECUTION))
        loop_thread_id = threading.get_ident()
        handler_thread_ids: list[int] = []
        transport = BufferingTransport()

        @app.route.get("/thread")
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

        @app.route.get("/cpu")
        def cpu_route(request) -> TextResponse:
            return TextResponse("CPU result: %s" % cpu_demo_work())

        try:
            response = await ASGIServer(app).handle_raw_request(build_get_request("/cpu"))
        finally:
            await app.close()

        expected = f"CPU result: {cpu_demo_work()}".encode("utf-8")
        self.assertTrue(response.endswith(b"\r\n\r\n" + expected))
