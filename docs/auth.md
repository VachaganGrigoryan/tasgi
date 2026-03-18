# Auth

## Built-in auth pieces

`tasgi` includes a small auth package with:

- `BearerTokenBackend`
- `APIKeyBackend`
- `BasicAuthBackend`
- `RequireAuthenticated`
- `RequireScope`
- `RequireRole`

## Global backend

```python
from tasgi import BearerTokenBackend, Identity, TasgiApp

def validate_token(token: str):
    if token == "demo-token":
        return Identity(subject="alice", scopes=frozenset({"profile"}))
    return None

app = TasgiApp(auth_backend=BearerTokenBackend(validate_token))
```

## Public and protected routes

```python
@app.route.get("/public", auth=False)
async def public_route(request):
    return {"public": True}

@app.route.get("/me", auth=True)
async def me(request):
    return {"subject": request.identity.subject}
```

## Scope-based protection

```python
from tasgi import RequireScope

@app.route.get("/admin", auth=RequireScope("admin"))
async def admin(request):
    return {"subject": request.identity.subject}
```

## Access auth on the request

Available in handlers:

- `request.auth`
- `request.identity`
- `request.user`

## Route-level backend override

```python
from tasgi import APIKeyBackend

service_backend = APIKeyBackend(
    lambda key: Identity(subject="service") if key == "service-key" else None
)

@app.route.get("/service", auth=True, auth_backend=service_backend)
async def service(request):
    return {"subject": request.identity.subject}
```
