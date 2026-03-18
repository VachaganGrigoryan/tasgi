"""Public router for the modular tasgi example app."""

from __future__ import annotations

from tasgi import Router

from models import AppInfoOut

router = Router(tags=["public"])


@router.get("/", summary="Application overview", response_model=AppInfoOut, auth=False)
async def overview(request) -> AppInfoOut:
    return AppInfoOut(
        service="tasgi modular api",
        version=request.app.config.version,
        docs_url="/docs",
        routers=["public", "tasks", "admin"],
    )
