"""Modular tasgi example app with routers defined outside the app factory."""

from __future__ import annotations

from tasgi import BearerTokenBackend, Identity, TasgiApp, TimingMiddleware

from routers.admin import router as admin_router
from routers.public import router as public_router
from routers.tasks import router as tasks_router
from services import TaskQueueService


def validate_token(token: str):
    if token == "demo-token":
        return Identity(subject="alice", scopes=frozenset({"tasks:read"}))
    if token == "writer-token":
        return Identity(subject="bob", scopes=frozenset({"tasks:read", "tasks:write"}))
    if token == "admin-token":
        return Identity(subject="admin", scopes=frozenset({"tasks:read", "tasks:write", "admin"}))
    return None


def build_app() -> TasgiApp:
    """Create the modular example application."""

    app = TasgiApp(
        host="127.0.0.1",
        port=8010,
        title="tasgi modular api",
        version="0.1.0a1",
        description="Example app showing routers, services, and app composition in separate modules.",
        docs=True,
        debug=True,
        auth_backend=BearerTokenBackend(validate_token, description="Use demo-token, writer-token, or admin-token."),
    )
    app.add_middleware(TimingMiddleware())

    @app.on_startup
    def startup(app_instance) -> None:
        app_instance.add_service("task_queue", TaskQueueService())

    @app.on_shutdown
    def shutdown(app_instance) -> None:
        app_instance.remove_service("task_queue")

    app.include_router(public_router)
    app.include_router(tasks_router, prefix="/api/tasks")
    app.include_router(admin_router, prefix="/api/admin")
    return app


app = build_app()
