# Runtime

## Model

`tasgi` is split into layers:

```text
socket/transport
  -> ASGI boundary
  -> tasgi runtime
  -> tasgi framework
  -> user handlers
```

## Rule

- network I/O stays on the event loop
- HTTP/WebSocket protocol handling stays on the event loop
- async handlers run on the event loop
- sync handlers run in worker threads

## Config

```python
from tasgi import TasgiApp, TasgiConfig

config = TasgiConfig(
    host="127.0.0.1",
    port=8000,
    debug=True,
    default_execution="thread",
    thread_pool_workers=8,
    cpu_thread_pool_workers=4,
)

app = TasgiApp(config=config)
```

## Serving

```python
from tasgi import serve

await serve(app)
```

Or:

```python
from tasgi import run

run(app)
```

## Startup and shutdown

```python
@app.on_startup
def startup(app):
    app.state.message = "ready"

@app.on_shutdown
def shutdown(app):
    del app.state.message
```

The app runtime starts before startup hooks and is cleaned up during shutdown.
