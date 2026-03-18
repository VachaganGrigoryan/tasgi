"""Stateful services for the advanced tasgi demo app."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
import time
from typing import Optional

from models import (
    ActivityEventOut,
    CreateOrderIn,
    HealthOut,
    MetricsOut,
    OrderLineOut,
    OrderOut,
    ProductDetailOut,
    ProductSummaryOut,
    RebuildIndexOut,
)


@dataclass
class _ProductRecord:
    sku: str
    name: str
    description: str
    price_cents: int
    stock: int
    tags: list[str]


def deterministic_cpu_job(iterations: int = 320_000) -> int:
    """Run a deterministic CPU-heavy workload for the demo ops route."""

    total = 0
    for index in range(iterations):
        total += ((index * index) ^ (index % 17)) % 10_007
    return total


class ActivityService:
    """Keep a small in-memory activity feed and runtime counters."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._started_at = time.monotonic()
        self._events: list[ActivityEventOut] = []
        self._sequence = 0
        self._websocket_clients = 0
        self._last_rebuild_checksum = 0

    def record(self, kind: str, message: str) -> ActivityEventOut:
        with self._lock:
            self._sequence += 1
            event = ActivityEventOut(
                sequence=self._sequence,
                kind=kind,
                message=message,
                at=_utc_now(),
            )
            self._events.append(event)
            self._events = self._events[-50:]
            return event

    def recent(self, *, limit: int = 8) -> list[ActivityEventOut]:
        with self._lock:
            return list(self._events[-limit:])

    def uptime_seconds(self) -> int:
        return int(time.monotonic() - self._started_at)

    def websocket_connected(self) -> None:
        with self._lock:
            self._websocket_clients += 1

    def websocket_disconnected(self) -> None:
        with self._lock:
            self._websocket_clients = max(0, self._websocket_clients - 1)

    def websocket_clients(self) -> int:
        with self._lock:
            return self._websocket_clients

    def set_last_rebuild_checksum(self, checksum: int) -> None:
        with self._lock:
            self._last_rebuild_checksum = checksum

    def last_rebuild_checksum(self) -> int:
        with self._lock:
            return self._last_rebuild_checksum


class CatalogService:
    """Manage an in-memory product catalog and stock reservations."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._products: dict[str, _ProductRecord] = {
            "sku-laptop-14": _ProductRecord(
                sku="sku-laptop-14",
                name="Atlas 14 Laptop",
                description="14-inch productivity laptop for hybrid teams.",
                price_cents=149_900,
                stock=12,
                tags=["hardware", "laptop", "flagship"],
            ),
            "sku-headset-pro": _ProductRecord(
                sku="sku-headset-pro",
                name="Northwind Pro Headset",
                description="Noise-isolating headset for support and sales teams.",
                price_cents=19_900,
                stock=28,
                tags=["hardware", "audio", "remote-work"],
            ),
            "sku-dock-usbc": _ProductRecord(
                sku="sku-dock-usbc",
                name="DockHub USB-C",
                description="Single-cable desk dock with power, video, and ethernet.",
                price_cents=24_900,
                stock=17,
                tags=["hardware", "accessory", "desktop"],
            ),
        }

    def list_products(self) -> list[ProductSummaryOut]:
        with self._lock:
            return [
                ProductSummaryOut(
                    sku=product.sku,
                    name=product.name,
                    price_cents=product.price_cents,
                    in_stock=product.stock,
                    tags=list(product.tags),
                )
                for product in self._products.values()
            ]

    def get_product(self, sku: str) -> Optional[ProductDetailOut]:
        with self._lock:
            product = self._products.get(sku)
            if product is None:
                return None
            return ProductDetailOut(
                sku=product.sku,
                name=product.name,
                description=product.description,
                price_cents=product.price_cents,
                in_stock=product.stock,
                tags=list(product.tags),
            )

    def reserve_items(self, items: list) -> list[OrderLineOut]:
        with self._lock:
            if not items:
                raise ValueError("Orders must contain at least one item.")

            requested: list[tuple[_ProductRecord, int]] = []
            for item in items:
                product = self._products.get(item.sku)
                if product is None:
                    raise KeyError(item.sku)
                if item.quantity <= 0:
                    raise ValueError("Item quantities must be positive.")
                if item.quantity > product.stock:
                    raise ValueError("Not enough stock for %s." % item.sku)
                requested.append((product, item.quantity))

            for product, quantity in requested:
                product.stock -= quantity

            return [
                OrderLineOut(
                    sku=product.sku,
                    name=product.name,
                    quantity=quantity,
                    unit_price_cents=product.price_cents,
                    line_total_cents=product.price_cents * quantity,
                )
                for product, quantity in requested
            ]

    def catalog_size(self) -> int:
        with self._lock:
            return len(self._products)


class OrdersService:
    """Manage in-memory orders with explicit locking."""

    def __init__(self, catalog: CatalogService, activity: ActivityService) -> None:
        self._catalog = catalog
        self._activity = activity
        self._lock = Lock()
        self._orders: dict[str, OrderOut] = {}
        self._order_sequence = 1000

    def create_order(self, customer_id: str, payload: CreateOrderIn) -> OrderOut:
        lines = self._catalog.reserve_items(payload.items)
        total_cents = sum(line.line_total_cents for line in lines)

        with self._lock:
            self._order_sequence += 1
            order = OrderOut(
                order_id="ord-%s" % self._order_sequence,
                customer_id=customer_id,
                status="accepted",
                total_cents=total_cents,
                created_at=_utc_now(),
                items=lines,
            )
            self._orders[order.order_id] = order

        self._activity.record(
            "orders.created",
            "Accepted %s for %s (%s cents)." % (order.order_id, customer_id, total_cents),
        )
        return order

    def list_orders(self, customer_id: Optional[str] = None) -> list[OrderOut]:
        with self._lock:
            orders = list(self._orders.values())
        orders.sort(key=lambda order: order.order_id, reverse=True)
        if customer_id is None:
            return orders
        return [order for order in orders if order.customer_id == customer_id]

    def get_order(self, order_id: str) -> Optional[OrderOut]:
        with self._lock:
            return self._orders.get(order_id)

    def total_orders(self) -> int:
        with self._lock:
            return len(self._orders)

    def active_orders(self) -> int:
        with self._lock:
            return sum(1 for order in self._orders.values() if order.status == "accepted")


class OpsService:
    """Aggregate operational snapshots from the demo services."""

    def __init__(
        self,
        catalog: CatalogService,
        orders: OrdersService,
        activity: ActivityService,
    ) -> None:
        self._catalog = catalog
        self._orders = orders
        self._activity = activity

    def health(self) -> HealthOut:
        return HealthOut(
            status="ok",
            uptime_seconds=self._activity.uptime_seconds(),
            catalog_items=self._catalog.catalog_size(),
            total_orders=self._orders.total_orders(),
            websocket_clients=self._activity.websocket_clients(),
            checks={
                "catalog": "ok",
                "orders": "ok",
                "activity": "ok",
            },
        )

    def metrics(self) -> MetricsOut:
        return MetricsOut(
            total_orders=self._orders.total_orders(),
            active_orders=self._orders.active_orders(),
            catalog_items=self._catalog.catalog_size(),
            recent_events=len(self._activity.recent(limit=50)),
            websocket_clients=self._activity.websocket_clients(),
            last_rebuild_checksum=self._activity.last_rebuild_checksum(),
        )

    def rebuild_index(self, *, iterations: int = 320_000) -> RebuildIndexOut:
        started = time.perf_counter()
        checksum = deterministic_cpu_job(iterations)
        duration_ms = int((time.perf_counter() - started) * 1000)
        self._activity.set_last_rebuild_checksum(checksum)
        self._activity.record(
            "ops.rebuild-index",
            "Search index rebuild finished with checksum %s." % checksum,
        )
        return RebuildIndexOut(
            job="search-index",
            checksum=checksum,
            duration_ms=duration_ms,
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

