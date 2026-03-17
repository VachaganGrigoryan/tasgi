"""Example tasgi application."""

from __future__ import annotations

import time

from tasgi import (
    THREAD_EXECUTION,
    JsonResponse,
    TimingMiddleware,
    TasgiApp,
    TextResponse,
)


def cpu_demo_work(iterations: int = 800_000) -> int:
    """Run a deterministic CPU-heavy workload for the demo route."""

    total = 0
    for index in range(iterations):
        total += (index * index) % 97
    return total


class MessageService:
    """Small demo service stored on application state."""

    def __init__(self, message: str) -> None:
        self.message = message


def build_demo_app() -> TasgiApp:
    """Create the example tasgi application."""

    app = TasgiApp(
        host="127.0.0.1",
        port=8000,
        debug=True,
        default_execution=THREAD_EXECUTION,
        thread_pool_workers=8,
    )
    app.add_middleware(TimingMiddleware())

    @app.on_startup
    def startup(app_instance) -> None:
        app_instance.add_service("message_service", MessageService("tasgi ready"))

    @app.on_shutdown
    def shutdown(app_instance) -> None:
        app_instance.remove_service("message_service")

    @app.get("/")
    async def home(request) -> TextResponse:
        message_service = request.service("message_service")
        return TextResponse(message_service.message)

    @app.get("/json")
    async def json_route(request) -> JsonResponse:
        message_service = request.service("message_service")
        return JsonResponse({"framework": "tasgi", "message": message_service.message})

    @app.post("/echo")
    def echo(request) -> TextResponse:
        return TextResponse(request.text())

    @app.get("/sleep")
    def sleep_route(request) -> TextResponse:
        time.sleep(1.0)
        return TextResponse("slept for 1.0 seconds")

    @app.get("/cpu")
    def cpu_route(request) -> TextResponse:
        return TextResponse("CPU result: %s" % cpu_demo_work())

    @app.get("/error")
    def error_route(request):
        raise RuntimeError("demo error")

    return app


app = build_demo_app()
