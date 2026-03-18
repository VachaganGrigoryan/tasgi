# Examples

## Included examples

Two example applications are included in the repository.

## `examples/service_api`

This is the more feature-rich example.

It demonstrates:

- HTTP routes
- auth-protected endpoints
- OpenAPI and Swagger UI
- streaming routes
- WebSocket support
- service registration on startup

Run it:

```bash
python3 examples/service_api/main.py
```

## `examples/modular_api`

This is the cleaner module-composition example.

It demonstrates:

- router modules defined outside the app factory
- `include_router(...)`
- shared services
- route grouping by feature

Run it:

```bash
python3 examples/modular_api/main.py
```

## Production-style router module

```python
from tasgi import Router

router = Router(tags=["tasks"])

@router.get("/")
async def list_tasks(request):
    service = request.service("task_queue")
    return {"items": service.list_tasks()}
```

Then include it in the app:

```python
from tasgi import TasgiApp
from routers.tasks import router as tasks_router

app = TasgiApp()
app.include_router(tasks_router, prefix="/api/tasks")
```
