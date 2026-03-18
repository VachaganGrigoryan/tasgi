# Threading

## Why tasgi uses threads

`tasgi` is designed to make sync Python handlers usable without moving network I/O off the event loop.

Good fits for thread execution:

- blocking libraries
- CPU-heavy toy or local workloads
- sync-first codebases moving toward ASGI gradually

## Default execution

```python
from tasgi import TasgiApp, THREAD_EXECUTION

app = TasgiApp(default_execution=THREAD_EXECUTION)
```

Now plain `def` routes naturally run in the thread runtime.

## Per-route override

```python
from tasgi import ASYNC_EXECUTION

@app.route.get("/status", execution=ASYNC_EXECUTION)
async def status(request):
    return {"ok": True}
```

## Important boundary

Worker threads do not write to sockets directly.

Instead:

1. handler logic runs in a worker thread
2. a `Response` is returned to the loop
3. the event loop writes bytes to the transport

## Example

```python
from tasgi import TasgiApp, TextResponse, THREAD_EXECUTION
import time

app = TasgiApp(default_execution=THREAD_EXECUTION)

@app.route.get("/sleep")
def sleep_route(request):
    time.sleep(0.25)
    return TextResponse("done")
```

## Shared state

Prefer:

- immutable config
- explicit services on `app.state`
- locking inside mutable shared services when needed
