# tasgi

`tasgi` means Thread ASGI. It is an experimental ASGI-compatible framework/runtime that keeps transport and protocol handling on the event loop while allowing handler execution on either the asyncio loop or a thread runtime.

## Status

`tasgi` is currently an alpha-stage project.

- public APIs are still settling
- auth APIs are still evolving
- router/module composition is still evolving
- native HTTP/2 support is still a prototype subset

Treat the current release line as experimental, not stable.

## Install

From source:

```bash
pip install -e .
```

After packaging to TestPyPI, the intended trial install flow is:

```bash
pip install --index-url https://test.pypi.org/simple/ tasgi
```

## Hello World

```python
from tasgi import TasgiApp, TextResponse

app = TasgiApp(docs=True, debug=True)

@app.route.get("/")
async def home(request):
    return TextResponse("hello from tasgi")
```

Run it:

```bash
tasgi
```

Or run the bundled demo app:

```bash
python3 examples/service_api/main.py
```

## Router Usage

`tasgi` now treats `app.route` as the main HTTP registration surface.

```python
from tasgi import JsonResponse, Router, TasgiApp

users = Router(tags=["users"])

@users.get("/users")
def list_users(request):
    return ["alice", "bob"]

app = TasgiApp()
app.include_router(users, prefix="/api")

@app.route.get("/status")
async def status(request):
    return JsonResponse({"ok": True})
```

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

## Dual Execution Model

App-level execution policy is explicit:

```python
from tasgi import ASYNC_EXECUTION, THREAD_EXECUTION, TasgiApp

app = TasgiApp(default_execution=THREAD_EXECUTION)

@app.route.get("/cpu")
def cpu(request):
    ...

@app.route.get("/status", execution=ASYNC_EXECUTION)
async def status(request):
    ...
```

## Request And Response Types

Main public HTTP types:

- `TasgiApp`
- `Router`
- `Request`
- `Response`
- `TextResponse`
- `JsonResponse`
- `StreamingResponse`

Handlers may return `Response` objects directly, or return typed values when a response model is declared.

## OpenAPI And Docs

Built-in docs can be enabled from app config:

```python
from dataclasses import dataclass
from tasgi import TasgiApp

@dataclass
class EchoIn:
    message: str

@dataclass
class EchoOut:
    echoed: str

app = TasgiApp(
    docs=True,
    title="tasgi demo",
    version="0.1.0a1",
)

@app.route.post("/echo", request_model=EchoIn, response_model=EchoOut)
def echo(request, body: EchoIn) -> EchoOut:
    return EchoOut(echoed=body.message)
```

Default docs endpoints:

- `/openapi.json`
- `/docs`

## Auth

`tasgi` includes an experimental pluggable auth layer.

Built-in starters:

- `BearerTokenBackend`
- `APIKeyBackend`
- `BasicAuthBackend`
- `RequireAuthenticated`
- `RequireScope`
- `RequireRole`

Example:

```python
from tasgi import BearerTokenBackend, Identity, RequireScope, TasgiApp

def validate_token(token: str):
    if token == "demo-token":
        return Identity(subject="alice", scopes=frozenset({"profile"}))
    if token == "admin-token":
        return Identity(subject="admin", scopes=frozenset({"admin"}))
    return None

app = TasgiApp(auth_backend=BearerTokenBackend(validate_token), docs=True)

@app.route.get("/public", auth=False)
async def public_route(request):
    return {"public": True}

@app.route.get("/me", auth=True)
async def me(request):
    return {"subject": request.identity.subject}

@app.route.get("/admin", auth=RequireScope("admin"))
async def admin(request):
    return {"subject": request.identity.subject}
```

Auth metadata is also reflected automatically in OpenAPI for built-in auth backends.

## Demo App

The bundled service example includes:

- HTTP routes
- router/module composition
- OpenAPI + Swagger UI
- auth examples
- streaming responses
- WebSocket echo
- thread-executed sync handlers

Run:

```bash
python3 examples/service_api/main.py
```

There is also a cleaner modular composition example:

```bash
python3 examples/modular_api/main.py
```

## Benchmarks

The benchmark suite exercises loopback TCP requests against a dedicated benchmark app.

Run:

```bash
python3 benchmarks/run_benchmarks.py
```

## Current Limits

- HTTP/2 support is prototype-grade, not production-complete
- auth API is still settling
- route registration APIs changed recently and may still evolve
- no middleware ecosystem yet
- no dependency injection container beyond explicit lightweight helpers
- no production-hardening claims

## License

`tasgi` is licensed under Apache-2.0. See `LICENSE` and `NOTICE`.

That keeps the project free to use, including in companies, while preserving
the attribution notices when the software is redistributed.

## Release Notes

This repository is currently prepared for an alpha-style release, not a stable release.

Before a public non-alpha release, the project still needs:

- final public API freeze
- repository/homepage URLs in package metadata
- a TestPyPI install-and-verify pass
