"""Example tasgi application."""

from __future__ import annotations

import asyncio
import time

from tasgi import (
    THREAD_EXECUTION,
    JsonResponse,
    Response,
    StreamingResponse,
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


async def require_http2(request, call_next):
    """Reject non-HTTP/2 requests so the demo exercises the HTTP/2 path explicitly."""

    if request.http_version != "2":
        return TextResponse(
            "This demo app requires HTTP/2. Try curl --http2-prior-knowledge http://127.0.0.1:8000/",
            status_code=505,
        )
    return await call_next(request)


def build_demo_app() -> TasgiApp:
    """Create the example tasgi application."""

    app = TasgiApp(
        host="127.0.0.1",
        port=8000,
        debug=True,
        default_execution=THREAD_EXECUTION,
        thread_pool_workers=8,
        http2=True,
    )
    app.add_middleware(TimingMiddleware())
    app.configure_docs(
        title="tasgi demo app",
        version="0.1.0",
        description="Example tasgi application demonstrating threading, streaming, HTTP/2, and WebSocket features.",
    )
    # app.add_middleware(require_http2)

    @app.on_startup
    def startup(app_instance) -> None:
        app_instance.add_service("message_service", MessageService("tasgi ready"))

    @app.on_shutdown
    def shutdown(app_instance) -> None:
        app_instance.remove_service("message_service")

    @app.get("/", metadata={"summary": "Home", "tags": ["demo"]})
    async def home(request) -> TextResponse:
        message_service = request.service("message_service")
        return TextResponse("%s over HTTP/%s" % (message_service.message, request.http_version))

    @app.get("/json", metadata={"summary": "JSON response", "tags": ["demo"]})
    async def json_route(request) -> JsonResponse:
        message_service = request.service("message_service")
        return JsonResponse(
            {
                "framework": "tasgi",
                "message": message_service.message,
                "http_version": request.http_version,
            }
        )

    @app.post(
        "/echo",
        metadata={
            "summary": "Echo request body",
            "description": "Return the incoming request body as plain text.",
            "tags": ["demo"],
        },
    )
    def echo(request) -> TextResponse:
        return TextResponse(request.text())

    @app.get("/sleep", metadata={"summary": "Blocking sleep", "tags": ["thread"]})
    def sleep_route(request) -> TextResponse:
        time.sleep(1.0)
        return TextResponse("slept for 1.0 seconds")

    @app.get("/cpu", metadata={"summary": "CPU-heavy thread route", "tags": ["thread"]})
    def cpu_route(request) -> TextResponse:
        return TextResponse("CPU result: %s" % cpu_demo_work())

    @app.get("/stream", metadata={"summary": "Async streaming response", "tags": ["streaming"]})
    async def stream_route(request) -> StreamingResponse:
        async def chunks():
            yield "async "
            await asyncio.sleep(0.05)
            yield "stream"

        return StreamingResponse(chunks(), media_type="text/plain; charset=utf-8")

    @app.get(
        "/thread-stream",
        metadata={"summary": "Threaded streaming response", "tags": ["streaming", "thread"]},
    )
    def thread_stream_route(request) -> StreamingResponse:
        def chunks():
            yield "thread "
            time.sleep(0.05)
            yield "stream"

        return StreamingResponse(chunks(), media_type="text/plain; charset=utf-8")

    @app.get("/error", metadata={"summary": "Raise demo exception", "tags": ["demo"]})
    def error_route(request):
        raise RuntimeError("demo error")

    @app.get(
        "/openapi.json",
        metadata={
            "summary": "OpenAPI document",
            "description": "Generated OpenAPI schema for the demo app.",
            "tags": ["docs"],
        },
    )
    async def openapi_route(request) -> JsonResponse:
        return JsonResponse(app.openapi_schema())

    @app.get(
        "/docs",
        metadata={
            "summary": "Swagger UI",
            "description": "Interactive Swagger UI for the generated OpenAPI schema.",
            "tags": ["docs"],
        },
    )
    async def docs_route(request) -> Response:
        return Response(_swagger_ui_html(), media_type="text/html; charset=utf-8")

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

    app.register_request_schema(
        "/echo",
        "POST",
        {"type": "string", "example": '{"a":1}'},
        media_type="text/plain",
        description="Raw request body echoed back by the server.",
    )
    app.register_response_schema(
        "/",
        "GET",
        200,
        {"type": "string"},
        media_type="text/plain",
        description="Plain-text hello response.",
    )
    app.register_response_schema(
        "/json",
        "GET",
        200,
        {
            "type": "object",
            "properties": {
                "framework": {"type": "string"},
                "message": {"type": "string"},
                "http_version": {"type": "string"},
            },
            "required": ["framework", "message", "http_version"],
        },
    )
    for path in ["/echo", "/sleep", "/cpu", "/stream", "/thread-stream", "/error"]:
        app.register_response_schema(
            path,
            "POST" if path == "/echo" else "GET",
            200 if path != "/error" else 500,
            {"type": "string"},
            media_type="text/plain",
        )
    app.register_response_schema("/openapi.json", "GET", 200, {"type": "object"})
    app.register_response_schema(
        "/docs",
        "GET",
        200,
        {"type": "string"},
        media_type="text/html",
    )

    return app


def _swagger_ui_html() -> str:
    return """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>tasgi demo docs</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
    <style>
      body { margin: 0; background: #faf7f1; }
      .topbar { display: none; }
    </style>
  </head>
  <body>
    <div id="swagger-ui"></div>
    <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
    <script>
      window.onload = function () {
        window.ui = SwaggerUIBundle({
          url: "/openapi.json",
          dom_id: "#swagger-ui",
          deepLinking: true,
          displayRequestDuration: true,
          presets: [SwaggerUIBundle.presets.apis]
        });
      };
    </script>
  </body>
</html>
"""


app = build_demo_app()
