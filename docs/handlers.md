# Handlers

## Async handlers

Async handlers run on the asyncio event loop.

```python
from tasgi import JsonResponse

@app.route.get("/json")
async def json_route(request):
    return JsonResponse({"ok": True})
```

## Sync handlers

Sync handlers run in the tasgi thread runtime.

```python
from tasgi import TextResponse

@app.route.get("/cpu")
def cpu_route(request):
    total = 0
    for index in range(50_000):
        total += (index * index) % 97
    return TextResponse(str(total))
```

## Request object

The handler receives a `Request` with:

- `request.method`
- `request.path`
- `request.query`
- `request.headers`
- `request.body`
- `request.route_params`
- `request.app`
- `request.auth`
- `request.identity`

Example:

```python
@app.route.post("/inspect")
async def inspect(request):
    return JsonResponse(
        {
            "text": request.text(),
            "json": request.json(),
            "content_type": request.header("content-type"),
        }
    )
```

## Response types

Use one of:

- `Response`
- `TextResponse`
- `JsonResponse`
- `StreamingResponse`

Example:

```python
from tasgi import Response, TextResponse

@app.route.get("/plain")
async def plain(request):
    return TextResponse("plain text")

@app.route.get("/custom")
async def custom(request):
    return Response(
        "created",
        status_code=201,
        headers=[("x-demo", "1")],
        media_type="text/plain; charset=utf-8",
    )
```
