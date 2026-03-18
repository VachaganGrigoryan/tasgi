# Routing

## Basic routes

`tasgi` uses `app.route` as the main HTTP registration API.

```python
from tasgi import TasgiApp, TextResponse

app = TasgiApp()

@app.route.get("/")
async def home(request):
    return TextResponse("home")

@app.route.post("/echo")
def echo(request):
    return TextResponse(request.text())
```

Supported helpers:

- `app.route.get(...)`
- `app.route.post(...)`
- `app.route.put(...)`
- `app.route.delete(...)`
- `app.route(...)` for generic registration

## Path params

```python
from tasgi import JsonResponse

@app.route.get("/users/{id}")
async def user_detail(request):
    return JsonResponse({"id": request.route_params["id"]})
```

## Separate routers

`Router` can be defined in other modules and included on the app.

```python
from tasgi import Router, TasgiApp, TextResponse

users_router = Router(tags=["users"])

@users_router.get("/users")
async def list_users(request):
    return TextResponse("users")

app = TasgiApp()
app.include_router(users_router, prefix="/api")
```

## Router-level docs defaults

Useful for module-level tags and shared error responses:

```python
users_router = Router(
    tags=["users"],
    responses={
        404: {
            "description": "User not found",
            "schema": {
                "type": "object",
                "properties": {"detail": {"type": "string"}},
            },
        }
    },
)
```

## Behavior

- exact path matches win before parameterized paths
- 404 is returned when no route matches
- 405 is returned when the path matches but the method does not
