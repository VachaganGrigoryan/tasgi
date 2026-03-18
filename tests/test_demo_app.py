"""Smoke tests for the bundled example applications."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
SERVICE_API_ROOT = PROJECT_ROOT / "examples" / "service_api"
MODULAR_API_ROOT = PROJECT_ROOT / "examples" / "modular_api"

for candidate in [str(SRC_ROOT), str(SERVICE_API_ROOT), str(MODULAR_API_ROOT)]:
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from tasgi.asgi_server import ASGIServer
from tasgi.main import _load_repo_service_app


def _load_module(module_name: str, root: Path):
    root_str = str(root)
    for candidate in [str(SERVICE_API_ROOT), str(MODULAR_API_ROOT)]:
        while candidate in sys.path:
            sys.path.remove(candidate)
    if str(SRC_ROOT) in sys.path:
        sys.path.remove(str(SRC_ROOT))
    sys.path.insert(0, str(SRC_ROOT))
    sys.path.insert(0, root_str)

    for candidate in [
        "models",
        "services",
        "routers",
        "routers.public",
        "routers.tasks",
        "routers.admin",
        module_name,
    ]:
        sys.modules.pop(candidate, None)
    spec = importlib.util.spec_from_file_location(module_name, root / "app.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load example app module.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_get_request(path: str) -> bytes:
    return f"GET {path} HTTP/1.1\r\nHost: example.test\r\n\r\n".encode("ascii")


def build_post_request(path: str, body: bytes) -> bytes:
    return (
        f"POST {path} HTTP/1.1\r\nHost: example.test\r\nContent-Length: {len(body)}\r\nContent-Type: application/json\r\n\r\n".encode(
            "ascii"
        )
        + body
    )


def with_header(request: bytes, name: str, value: str) -> bytes:
    return request.replace(b"\r\n\r\n", f"\r\n{name}: {value}\r\n\r\n".encode("latin-1"), 1)


class ServiceAPIExampleTests(unittest.IsolatedAsyncioTestCase):
    async def test_overview_and_catalog_routes(self) -> None:
        demo_app = _load_module("service_api_example", SERVICE_API_ROOT).build_app()

        try:
            overview_response = await ASGIServer(demo_app).handle_raw_request(build_get_request("/"))
            catalog_response = await ASGIServer(demo_app).handle_raw_request(build_get_request("/api/catalog/products"))
        finally:
            await demo_app.close()

        self.assertIn(b'"service": "tasgi service api"', overview_response)
        self.assertIn(b'"sku": "sku-laptop-14"', catalog_response)

    async def test_authenticated_order_flow(self) -> None:
        demo_app = _load_module("service_api_example_orders", SERVICE_API_ROOT).build_app()

        try:
            create_response = await ASGIServer(demo_app).handle_raw_request(
                with_header(
                    build_post_request(
                        "/api/orders",
                        b'{"items":[{"sku":"sku-laptop-14","quantity":1}]}',
                    ),
                    "Authorization",
                    "Bearer demo-token",
                )
            )
            list_response = await ASGIServer(demo_app).handle_raw_request(
                with_header(build_get_request("/api/orders"), "Authorization", "Bearer demo-token")
            )
        finally:
            await demo_app.close()

        self.assertIn(b"HTTP/1.1 201 Created", create_response)
        self.assertIn(b'"customer_id": "alice"', create_response)
        self.assertIn(b'"order_id": "ord-', list_response)

    async def test_ops_routes_enforce_auth_and_scope(self) -> None:
        demo_app = _load_module("service_api_example_ops", SERVICE_API_ROOT).build_app()

        try:
            unauthorized_metrics = await ASGIServer(demo_app).handle_raw_request(build_get_request("/api/ops/metrics"))
            ops_metrics = await ASGIServer(demo_app).handle_raw_request(
                with_header(build_get_request("/api/ops/metrics"), "Authorization", "Bearer ops-token")
            )
            forbidden_job = await ASGIServer(demo_app).handle_raw_request(
                with_header(build_post_request("/api/ops/jobs/rebuild-search-index", b"{}"), "Authorization", "Bearer ops-token")
            )
            admin_job = await ASGIServer(demo_app).handle_raw_request(
                with_header(build_post_request("/api/ops/jobs/rebuild-search-index", b"{}"), "Authorization", "Bearer admin-token")
            )
        finally:
            await demo_app.close()

        self.assertIn(b"HTTP/1.1 401 Unauthorized", unauthorized_metrics)
        self.assertIn(b'"total_orders":', ops_metrics)
        self.assertIn(b"HTTP/1.1 403 Forbidden", forbidden_job)
        self.assertIn(b'"job": "search-index"', admin_job)


class ModularAPIExampleTests(unittest.IsolatedAsyncioTestCase):
    async def test_modular_app_public_and_task_routes(self) -> None:
        modular_app = _load_module("modular_api_example", MODULAR_API_ROOT).build_app()

        try:
            public_response = await ASGIServer(modular_app).handle_raw_request(build_get_request("/"))
            list_response = await ASGIServer(modular_app).handle_raw_request(
                with_header(build_get_request("/api/tasks"), "Authorization", "Bearer demo-token")
            )
            create_response = await ASGIServer(modular_app).handle_raw_request(
                with_header(
                    build_post_request("/api/tasks", b'{"title":"Ship weekly report","owner":"ops"}'),
                    "Authorization",
                    "Bearer writer-token",
                )
            )
        finally:
            await modular_app.close()

        self.assertIn(b'"service": "tasgi modular api"', public_response)
        self.assertIn(b'"task_id": "task-001"', list_response)
        self.assertIn(b"HTTP/1.1 201 Created", create_response)
        self.assertIn(b'"title": "Ship weekly report"', create_response)


class ExampleLoaderTests(unittest.TestCase):
    def test_repo_service_example_loader_returns_app(self) -> None:
        app = _load_repo_service_app()
        self.assertIsNotNone(app)
        self.assertTrue(hasattr(app, "config"))
