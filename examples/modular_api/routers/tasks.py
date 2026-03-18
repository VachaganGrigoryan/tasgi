"""Task router for the modular tasgi example app."""

from __future__ import annotations

from tasgi import APP_SCOPE, Depends, RequireScope, Router

from models import TaskCreateIn, TaskOut
from services import TaskQueueService

router = Router(
    tags=["tasks"],
    responses={
        401: {"description": "Authentication required", "schema": {"type": "object", "properties": {"detail": {"type": "string"}}}},
        403: {"description": "Permission denied", "schema": {"type": "object", "properties": {"detail": {"type": "string"}}}},
    },
)


def get_queue(app) -> TaskQueueService:
    return app.require_service("task_queue")


@router.get("/", summary="List tasks", auth=True, response_model=list[TaskOut])
def list_tasks(request, queue=Depends(get_queue, scope=APP_SCOPE)) -> list[TaskOut]:
    del request
    return queue.list_tasks()


@router.post(
    "/",
    summary="Create task",
    auth=RequireScope("tasks:write"),
    request_model=TaskCreateIn,
    response_model=TaskOut,
    status_code=201,
)
def create_task(request, body: TaskCreateIn, queue=Depends(get_queue, scope=APP_SCOPE)) -> TaskOut:
    del request
    return queue.create_task(body)
