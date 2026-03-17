# tasgi

`tasgi` means Thread ASGI. It is a small ASGI-compatible framework/runtime that keeps transport and protocol handling on the event loop while allowing handler execution on either the asyncio loop or a thread runtime.

## Core Model

`tasgi` is layered like this:

```text
socket/transport
  -> ASGI boundary
  -> tasgi runtime
  -> tasgi framework
  -> user handlers
```

Rules:

- network I/O stays on the event loop
- HTTP protocol handling stays on the event loop
- async handlers run on the asyncio loop
- sync handlers run in the tasgi thread runtime
- worker threads never write directly to sockets

## Main Types

- `TasgiConfig`: app/runtime settings
- `TasgiApp`: app object, router, state, lifecycle hooks, and ASGI entrypoint
- `Request`: buffered request object passed to handlers
- `Response`: base response type
- `TextResponse`: plain text response
- `JsonResponse`: JSON response

## Example

```python
from tasgi import (
    ASYNC_EXECUTION,
    THREAD_EXECUTION,
    JsonResponse,
    TasgiApp,
    TasgiConfig,
    TextResponse,
)

app = TasgiApp(
    config=TasgiConfig(
        host="127.0.0.1",
        port=8000,
        debug=True,
        default_execution=THREAD_EXECUTION,
        thread_pool_workers=8,
    )
)

@app.on_startup
def startup(app):
    app.state.message = "tasgi ready"

@app.get("/", execution=ASYNC_EXECUTION)
async def home(request):
    return TextResponse(request.app.state.message)

@app.get("/json", execution=ASYNC_EXECUTION)
async def get_json(request):
    return JsonResponse({"framework": "tasgi"})

@app.post("/echo", execution=THREAD_EXECUTION)
def echo(request):
    return TextResponse(request.text())
```

## Dual Execution Model

`tasgi` supports two app-level styles:

- hybrid async-first mode
  - configure `TasgiConfig(default_execution="async")`
  - async handlers run on the event loop
  - sync handlers run in the thread runtime
- thread-first mode
  - configure `TasgiConfig(default_execution="thread")`
  - sync handlers are the default model
  - async handlers must opt in with `execution="async"`

Route-level override is explicit:

```python
@app.get("/cpu", execution=THREAD_EXECUTION)
def cpu(request):
    ...

@app.get("/status", execution=ASYNC_EXECUTION)
async def status(request):
    ...
```

## Lifecycle And State

`TasgiApp` supports startup and shutdown hooks:

```python
@app.on_startup
def startup(app):
    app.state.message = "ready"

@app.on_shutdown
async def shutdown(app):
    ...
```

`app.state` is a small thread-safe container for shared app-wide state such as immutable config references, service objects, caches, or loggers. Shared mutable state should still be used deliberately.

## HTTP Handling

For each request `tasgi`:

1. validates the HTTP ASGI scope
2. buffers the request body
3. builds a `Request`
4. resolves the route
5. chooses event-loop or thread execution
6. runs the handler
7. serializes a complete ASGI response

Every response path emits:

- `http.response.start`
- final `http.response.body` with `more_body=False`

Framework errors are handled like this:

- `404 Not Found` for unmatched paths
- `405 Method Not Allowed` with `Allow` header for method mismatch
- `500 Internal Server Error` for unhandled exceptions
- debug mode includes simple error text

## Architecture

```text
socket -> asyncio transport -> request parser -> ASGI scope
                                           -> tasgi app
                                           -> router
                                           -> async handler on loop
                                           -> sync handler in thread pool
                                           -> response -> ASGI messages -> writer -> socket
```

## Project Layout

```text
tasgi/
  README.md
  pyproject.toml
  src/
    tasgi/
      __init__.py
      app.py
      asgi.py
      asgi_server.py
      config.py
      exceptions.py
      http_parser.py
      lifecycle.py
      main.py
      request.py
      response.py
      routing.py
      runtime.py
      state.py
      types.py
  examples/
    demo_app/
      app.py
      main.py
  tests/
    test_asgi_server.py
    test_http_parser.py
    test_tasgi_app.py
```

## Running The Demo App

From the project root:

```bash
python3 examples/demo_app/main.py
```

Defaults:

- host: `127.0.0.1`
- port: `8000`

## Demo Endpoints

```bash
curl http://127.0.0.1:8000/
curl http://127.0.0.1:8000/json
curl -X POST http://127.0.0.1:8000/echo -d '{"a":1}'
curl http://127.0.0.1:8000/sleep
curl http://127.0.0.1:8000/cpu
curl http://127.0.0.1:8000/error
```

## Current Limits

- HTTP/1.1 only
- only `GET` and `POST`
- one request per connection
- fully buffered request body
- fully buffered response body
- no chunked transfer encoding
- no keep-alive reuse
- no WebSockets
- no full ASGI lifespan protocol yet
- no middleware stack
- no dependency injection
- no path parameters
- no streaming request/response bodies
- no HTTP/2
- no TLS

The goal is a clear, predictable core that can grow into middleware, lifespan, path params, and broader protocol support later.
