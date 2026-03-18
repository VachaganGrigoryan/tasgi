"""Typed models used by the advanced tasgi demo app."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DemoOverviewOut:
    service: str
    version: str
    environment: str
    http_version: str
    docs_url: str
    openapi_url: str
    websocket_path: str
    sample_routes: list[str]


@dataclass
class ProductSummaryOut:
    sku: str
    name: str
    price_cents: int
    in_stock: int
    tags: list[str] = field(default_factory=list)


@dataclass
class ProductDetailOut:
    sku: str
    name: str
    description: str
    price_cents: int
    in_stock: int
    tags: list[str] = field(default_factory=list)


@dataclass
class OrderItemIn:
    sku: str
    quantity: int


@dataclass
class CreateOrderIn:
    items: list[OrderItemIn]


@dataclass
class OrderLineOut:
    sku: str
    name: str
    quantity: int
    unit_price_cents: int
    line_total_cents: int


@dataclass
class OrderOut:
    order_id: str
    customer_id: str
    status: str
    total_cents: int
    created_at: str
    items: list[OrderLineOut]


@dataclass
class SessionOut:
    subject: str
    display_name: str
    roles: list[str]
    scopes: list[str]
    backend: str
    scheme: str
    request_label: str


@dataclass
class PublicStatusOut:
    public: bool
    authenticated: bool
    docs_url: str
    websocket_path: str


@dataclass
class HealthOut:
    status: str
    uptime_seconds: int
    catalog_items: int
    total_orders: int
    websocket_clients: int
    checks: dict[str, str]


@dataclass
class MetricsOut:
    total_orders: int
    active_orders: int
    catalog_items: int
    recent_events: int
    websocket_clients: int
    last_rebuild_checksum: int


@dataclass
class CpuCheckOut:
    job: str
    iterations: int
    checksum: int


@dataclass
class ActivityEventOut:
    sequence: int
    kind: str
    message: str
    at: str


@dataclass
class RebuildIndexOut:
    job: str
    checksum: int
    duration_ms: int
