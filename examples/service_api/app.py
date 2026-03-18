"""Advanced service-style tasgi example application."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import json

from models import (
    ActivityEventOut,
    CreateOrderIn,
    DemoOverviewOut,
    HealthOut,
    MetricsOut,
    OrderOut,
    ProductDetailOut,
    ProductSummaryOut,
    PublicStatusOut,
    RebuildIndexOut,
    SessionOut,
)
from services import ActivityService, CatalogService, OpsService, OrdersService
from tasgi import (
    APP_SCOPE,
    BearerTokenBackend,
    Depends,
    Identity,
    JsonResponse,
    RequireScope,
    Router,
    StreamingResponse,
    THREAD_EXECUTION,
    TimingMiddleware,
    TasgiApp,
)


ERROR_SCHEMA = {
    "type": "object",
    "properties": {
        "detail": {"type": "string"},
    },
    "required": ["detail"],
}


def get_catalog(app) -> CatalogService:
    return app.require_service("catalog")


def get_orders(app) -> OrdersService:
    return app.require_service("orders")


def get_activity(app) -> ActivityService:
    return app.require_service("activity")


def get_ops(app) -> OpsService:
    return app.require_service("ops")


def get_request_label(request) -> str:
    return "HTTP/%s" % request.http_version


def validate_demo_token(token: str):
    """Resolve demo tokens into identities with realistic scopes."""

    if token == "demo-token":
        return Identity(
            subject="alice",
            display_name="Alice Example",
            roles=frozenset({"customer"}),
            scopes=frozenset({"profile", "orders"}),
        )
    if token == "ops-token":
        return Identity(
            subject="ops",
            display_name="Operations Viewer",
            roles=frozenset({"ops"}),
            scopes=frozenset({"profile", "orders", "metrics"}),
        )
    if token == "admin-token":
        return Identity(
            subject="admin",
            display_name="Admin Example",
            roles=frozenset({"admin"}),
            scopes=frozenset({"profile", "orders", "metrics", "admin"}),
        )
    return None


def build_app() -> TasgiApp:
    """Create the advanced service-style tasgi example application."""

    auth_router = Router(
        tags=["auth"],
        responses={
            401: {"description": "Authentication required", "schema": ERROR_SCHEMA},
            403: {"description": "Permission denied", "schema": ERROR_SCHEMA},
        },
    )
    catalog_router = Router(
        tags=["catalog"],
        responses={
            404: {"description": "Catalog item not found", "schema": ERROR_SCHEMA},
        },
    )
    orders_router = Router(
        tags=["orders"],
        responses={
            400: {"description": "Invalid order request", "schema": ERROR_SCHEMA},
            401: {"description": "Authentication required", "schema": ERROR_SCHEMA},
            403: {"description": "Permission denied", "schema": ERROR_SCHEMA},
            404: {"description": "Order not found", "schema": ERROR_SCHEMA},
        },
    )
    ops_router = Router(
        tags=["ops"],
        responses={
            401: {"description": "Authentication required", "schema": ERROR_SCHEMA},
            403: {"description": "Permission denied", "schema": ERROR_SCHEMA},
        },
    )

    app = TasgiApp(
        host="127.0.0.1",
        port=8000,
        title="tasgi service api",
        version="0.1.0a1",
        description="Advanced tasgi example showing realistic service routes, auth, docs, streaming, thread-aware handlers, and realtime notifications.",
        debug=True,
        docs=True,
        default_execution=THREAD_EXECUTION,
        thread_pool_workers=8,
        http2=True,
        auth_backend=BearerTokenBackend(
            validate_demo_token,
            bearer_format="opaque token",
            description="Use demo-token, ops-token, or admin-token.",
        ),
    )
    app.add_middleware(TimingMiddleware())

    @app.on_startup
    def startup(app_instance) -> None:
        catalog = CatalogService()
        activity = ActivityService()
        orders = OrdersService(catalog, activity)
        ops = OpsService(catalog, orders, activity)

        app_instance.add_service("catalog", catalog)
        app_instance.add_service("activity", activity)
        app_instance.add_service("orders", orders)
        app_instance.add_service("ops", ops)

        activity.record("system.startup", "Demo services started.")

    @app.on_shutdown
    def shutdown(app_instance) -> None:
        activity = app_instance.get_service("activity")
        if activity is not None:
            activity.record("system.shutdown", "Demo services stopping.")
        app_instance.remove_service("ops")
        app_instance.remove_service("orders")
        app_instance.remove_service("activity")
        app_instance.remove_service("catalog")

    @app.route.get("/", summary="Service overview", tags=["demo"], auth=False, response_model=DemoOverviewOut)
    async def home(request) -> DemoOverviewOut:
        return DemoOverviewOut(
            service="tasgi service api",
            version=request.app.config.version,
            environment="local",
            http_version=request.http_version,
            docs_url="/docs",
            openapi_url="/openapi.json",
            websocket_path="/ws/notifications",
            sample_routes=[
                "/api/catalog/products",
                "/api/orders",
                "/api/ops/health",
                "/api/ops/events/stream",
            ],
        )

    @auth_router.get("/public", summary="Public service status", auth=False, response_model=PublicStatusOut)
    async def public_route(request) -> PublicStatusOut:
        return PublicStatusOut(
            public=True,
            authenticated=bool(request.auth and request.auth.is_authenticated),
            docs_url="/docs",
            websocket_path="/ws/notifications",
        )

    @auth_router.get("/me", summary="Current authenticated session", auth=True, response_model=SessionOut)
    async def me_route(
        request,
        request_label=Depends(get_request_label),
    ) -> SessionOut:
        assert request.auth is not None
        assert request.identity is not None
        return SessionOut(
            subject=request.identity.subject,
            display_name=request.identity.display_name or request.identity.subject,
            roles=sorted(request.identity.roles),
            scopes=sorted(request.identity.scopes),
            backend=request.auth.backend or "unknown",
            scheme=request.auth.scheme or "unknown",
            request_label=request_label,
        )

    @auth_router.get(
        "/admin",
        summary="Admin capability check",
        auth=RequireScope("admin"),
        response_model=dict[str, str],
    )
    async def admin_route(request) -> dict[str, str]:
        assert request.identity is not None
        return {
            "subject": request.identity.subject,
            "access": "admin",
        }

    @catalog_router.get("/products", summary="List catalog products", auth=False, response_model=list[ProductSummaryOut])
    def list_products(
        request,
        catalog=Depends(get_catalog, scope=APP_SCOPE),
    ) -> list[ProductSummaryOut]:
        del request
        return catalog.list_products()

    @catalog_router.get("/products/{sku}", summary="Get one product", auth=False, response_model=ProductDetailOut)
    def get_product(
        request,
        catalog=Depends(get_catalog, scope=APP_SCOPE),
    ):
        product = catalog.get_product(request.route_params["sku"])
        if product is None:
            return JsonResponse({"detail": "Unknown product %s." % request.route_params["sku"]}, status_code=404)
        return product

    @orders_router.get("/", summary="List current customer orders", auth=True, response_model=list[OrderOut])
    def list_orders(
        request,
        orders=Depends(get_orders, scope=APP_SCOPE),
    ) -> list[OrderOut]:
        assert request.identity is not None
        customer_id = None if _is_admin(request.identity) else request.identity.subject
        return orders.list_orders(customer_id=customer_id)

    @orders_router.post(
        "/",
        summary="Create a new order",
        auth=True,
        request_model=CreateOrderIn,
        response_model=OrderOut,
        status_code=201,
    )
    def create_order(
        request,
        body: CreateOrderIn,
        orders=Depends(get_orders, scope=APP_SCOPE),
    ):
        assert request.identity is not None
        try:
            return orders.create_order(request.identity.subject, body)
        except KeyError as exc:
            return JsonResponse({"detail": "Unknown product %s." % exc.args[0]}, status_code=404)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status_code=400)

    @orders_router.get("/{order_id}", summary="Get one order", auth=True, response_model=OrderOut)
    def get_order(
        request,
        orders=Depends(get_orders, scope=APP_SCOPE),
    ):
        assert request.identity is not None
        order = orders.get_order(request.route_params["order_id"])
        if order is None:
            return JsonResponse({"detail": "Unknown order %s." % request.route_params["order_id"]}, status_code=404)
        if not _is_admin(request.identity) and order.customer_id != request.identity.subject:
            return JsonResponse({"detail": "You do not have access to this order."}, status_code=403)
        return order

    @ops_router.get("/health", summary="Public health snapshot", auth=False, response_model=HealthOut)
    async def health_route(
        request,
        ops=Depends(get_ops, scope=APP_SCOPE),
    ) -> HealthOut:
        del request
        return ops.health()

    @ops_router.get("/metrics", summary="Operations metrics", auth=RequireScope("metrics"), response_model=MetricsOut)
    async def metrics_route(
        request,
        ops=Depends(get_ops, scope=APP_SCOPE),
    ) -> MetricsOut:
        del request
        return ops.metrics()

    @ops_router.get("/events", summary="Recent activity feed", auth=RequireScope("metrics"), response_model=list[ActivityEventOut])
    async def events_route(
        request,
        activity=Depends(get_activity, scope=APP_SCOPE),
    ) -> list[ActivityEventOut]:
        del request
        return activity.recent(limit=12)

    @ops_router.get("/events/stream", summary="Realtime activity stream", auth=True)
    async def events_stream_route(
        request,
        activity=Depends(get_activity, scope=APP_SCOPE),
        ops=Depends(get_ops, scope=APP_SCOPE),
    ) -> StreamingResponse:
        async def chunks():
            for event in activity.recent(limit=5):
                yield "event: activity\ndata: %s\n\n" % json.dumps(asdict(event))
                await asyncio.sleep(0.05)
            yield "event: metrics\ndata: %s\n\n" % json.dumps(asdict(ops.metrics()))
            await asyncio.sleep(0.05)
            yield "event: done\ndata: stream complete\n\n"

        return StreamingResponse(chunks(), media_type="text/event-stream")

    @ops_router.post(
        "/jobs/rebuild-search-index",
        summary="Run a CPU-heavy rebuild job",
        auth=RequireScope("admin"),
        response_model=RebuildIndexOut,
    )
    def rebuild_search_index(
        request,
        ops=Depends(get_ops, scope=APP_SCOPE),
    ) -> RebuildIndexOut:
        del request
        return ops.rebuild_index()

    app.include_router(auth_router)
    app.include_router(catalog_router, prefix="/api/catalog")
    app.include_router(orders_router, prefix="/api/orders")
    app.include_router(ops_router, prefix="/api/ops")

    @app.websocket("/ws/notifications")
    async def notifications_socket(websocket) -> None:
        activity = websocket.service("activity")
        ops = websocket.service("ops")
        activity.websocket_connected()
        activity.record("ws.connected", "Realtime dashboard client connected.")

        await websocket.accept()
        await websocket.send_text(
            json.dumps(
                {
                    "type": "welcome",
                    "message": "Send metrics, events, or ping.",
                }
            )
        )
        await websocket.send_text(json.dumps({"type": "metrics", "payload": asdict(ops.metrics())}))

        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if "text" not in message:
                    await websocket.send_text(json.dumps({"type": "error", "message": "Text messages only."}))
                    continue

                command = str(message["text"]).strip().lower()
                if command == "metrics":
                    await websocket.send_text(json.dumps({"type": "metrics", "payload": asdict(ops.metrics())}))
                elif command == "events":
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "events",
                                "payload": [asdict(event) for event in activity.recent(limit=8)],
                            }
                        )
                    )
                elif command == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
                else:
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "help",
                                "message": "Available commands: metrics, events, ping",
                            }
                        )
                    )
        finally:
            activity.record("ws.disconnected", "Realtime dashboard client disconnected.")
            activity.websocket_disconnected()

    return app


def _is_admin(identity: Identity) -> bool:
    return "admin" in identity.scopes or "admin" in identity.roles


app = build_app()
