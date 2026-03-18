# tasgi Docs

`tasgi` is an experimental ASGI-compatible framework/runtime focused on one main idea:

- keep transport and protocol handling on the event loop
- let handlers run either on the event loop or in worker threads

## Main features

- `TasgiApp` with `Router` support
- async and sync handler execution
- built-in OpenAPI and Swagger UI
- request/response helpers
- auth backends and auth policies
- native HTTP/2 prototype transport
- WebSocket support
- unittest-first test suite

## Quick start

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

Or run one of the bundled examples:

```bash
python3 examples/service_api/main.py
python3 examples/modular_api/main.py
```

## Docs map

- [Getting Started](getting-started.md)
- [Routing](routing.md)
- [Handlers](handlers.md)
- [Runtime](runtime.md)
- [Threading](threading.md)
- [HTTP/2](http2.md)
- [WebSocket](websocket.md)
- [OpenAPI](openapi.md)
- [Auth](auth.md)
- [Testing](testing.md)
- [Deployment](deployment.md)
- [Examples](examples.md)

## Notes

- `tasgi` runtime code is stdlib-only
- docs and packaging may use external tools like `coverage`, `build`, and `twine`
- the HTTP/2 implementation is still a prototype subset, not a production-grade stack
