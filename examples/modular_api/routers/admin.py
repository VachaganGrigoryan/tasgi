"""Admin router for the modular tasgi example app."""

from __future__ import annotations

from tasgi import APP_SCOPE, Depends, RequireScope, Router

from models import QueueStatsOut
from services import TaskQueueService

router = Router(tags=["admin"])


def get_queue(app) -> TaskQueueService:
    return app.require_service("task_queue")


@router.get("/stats", summary="Queue stats", auth=RequireScope("admin"), response_model=QueueStatsOut)
def queue_stats(request, queue=Depends(get_queue, scope=APP_SCOPE)) -> QueueStatsOut:
    del request
    return queue.stats()
