# OpenAPI And Docs

## Enable built-in docs

```python
from tasgi import TasgiApp

app = TasgiApp(
    docs=True,
    title="tasgi demo",
    version="0.1.0a1",
)
```

Built-in endpoints:

- `/openapi.json`
- `/docs`

## Route metadata

Use decorator arguments instead of manual metadata dicts when possible.

```python
from dataclasses import dataclass

@dataclass
class EchoIn:
    message: str

@dataclass
class EchoOut:
    echoed: str

@app.route.post(
    "/echo",
    summary="Echo message",
    tags=["demo"],
    request_model=EchoIn,
    response_model=EchoOut,
    status_code=201,
)
def echo(request, body: EchoIn) -> EchoOut:
    return EchoOut(echoed=body.message)
```

## Manual schema overrides

If automatic inference is not enough, use the registration helpers:

```python
app.register_request_schema(
    "/echo",
    "POST",
    {"type": "object", "properties": {"message": {"type": "string"}}},
)

app.register_response_schema(
    "/echo",
    "POST",
    201,
    {"type": "object", "properties": {"echoed": {"type": "string"}}},
)
```

## Access the schema directly

```python
document = app.openapi_schema()
```

## Current scope

- HTTP routes are included
- WebSocket routes are excluded
- auth schemes from built-in backends are added automatically
