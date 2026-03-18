"""Example tasgi application."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import time

from tasgi import (
    APP_SCOPE,
    THREAD_EXECUTION,
    Depends,
    JsonResponse,
    Router,
    StreamingResponse,
    TimingMiddleware,
    TasgiApp,
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


@dataclass
class EchoIn:
    message: str


@dataclass
class EchoOut:
    echoed: str


def get_message_service(app) -> MessageService:
    return app.require_service("message_service")


def get_request_label(request) -> str:
    return "HTTP/%s" % request.http_version


async def require_http2(request, call_next):
    """Reject non-HTTP/2 requests so the demo exercises the HTTP/2 path explicitly."""

    if request.http_version != "2":
        return JsonResponse(
            {
                "error": "This demo app requires HTTP/2. Try curl --http2-prior-knowledge http://127.0.0.1:8000/",
            },
            status_code=505,
        )
    return await call_next(request)


def build_demo_app() -> TasgiApp:
    """Create the example tasgi application."""

    users_router = Router(
        tags=["users"],
        responses={
            404: {
                "description": "User not found",
                "schema": {
                    "type": "object",
                    "properties": {
                        "detail": {"type": "string"},
                    },
                    "required": ["detail"],
                },
            }
        },
    )

    @users_router.get(
        "/users",
        summary="List users",
        response_model=list[str],
    )
    def list_users(request) -> list[str]:
        return ["alice", "bob", "carol"]

    app = TasgiApp(
        host="127.0.0.1",
        port=8000,
        title="tasgi demo app",
        version="0.1.0",
        description="Example tasgi application demonstrating threading, streaming, HTTP/2, built-in docs, and WebSocket features.",
        debug=True,
        docs=True,
        default_execution=THREAD_EXECUTION,
        thread_pool_workers=8,
        http2=True,
    )
    app.add_middleware(TimingMiddleware())
    # app.add_middleware(require_http2)
    app.include_router(users_router, prefix="/api")

    @app.on_startup
    def startup(app_instance) -> None:
        app_instance.add_service("message_service", MessageService("tasgi ready"))

    @app.on_shutdown
    def shutdown(app_instance) -> None:
        app_instance.remove_service("message_service")

    @app.route.get("/", summary="Home", tags=["demo"], response_model=str)
    async def home(
        request,
        message_service=Depends(get_message_service, scope=APP_SCOPE),
        request_label=Depends(get_request_label),
    ) -> str:
        return "%s over %s" % (message_service.message, request_label)

    @app.route.get("/json", summary="JSON response", tags=["demo"], response_model=dict[str, str])
    async def json_route(
        request,
        message_service=Depends(get_message_service, scope=APP_SCOPE),
        request_label=Depends(get_request_label),
    ) -> dict[str, str]:
        return {
            "framework": "tasgi",
            "message": message_service.message,
            "http_version": request.http_version,
            "label": request_label,
        }

    @app.route.post(
        "/echo",
        summary="Echo message",
        description="Decode a JSON body into a dataclass and echo it back.",
        tags=["demo"],
        request_model=EchoIn,
        response_model=EchoOut,
        status_code=201,
    )
    def echo(request, body: EchoIn) -> EchoOut:
        return EchoOut(echoed=body.message)

    @app.route.get("/sleep", summary="Blocking sleep", tags=["thread"], response_model=str)
    def sleep_route(request) -> str:
        time.sleep(1.0)
        return "slept for 1.0 seconds"

    @app.route.get("/cpu", summary="CPU-heavy thread route", tags=["thread"], response_model=str)
    def cpu_route(request) -> str:
        return "CPU result: %s" % cpu_demo_work()

    @app.route.get("/stream", summary="Async streaming response", tags=["streaming"])
    async def stream_route(request) -> StreamingResponse:
        async def chunks():
            yield "async "
            await asyncio.sleep(0.05)
            yield "stream"

        return StreamingResponse(chunks(), media_type="text/plain; charset=utf-8")

    @app.route.get("/thread-stream", summary="Threaded streaming response", tags=["streaming", "thread"])
    def thread_stream_route(request) -> StreamingResponse:
        def chunks():
            yield "thread "
            time.sleep(0.05)
            yield "stream"

        return StreamingResponse(chunks(), media_type="text/plain; charset=utf-8")

    @app.route.get("/error", summary="Raise demo exception", tags=["demo"], response_model=str, status_code=500)
    def error_route(request):
        raise RuntimeError("demo error")

    @app.websocket("/ws")
    async def websocket_echo(websocket) -> None:
        await websocket.accept()
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if "text" in message:
                await websocket.send_text("echo:%s" % message["text"])
            elif "bytes" in message:
                await websocket.send_bytes(b"echo:" + bytes(message["bytes"]))

    return app


app = build_demo_app()
