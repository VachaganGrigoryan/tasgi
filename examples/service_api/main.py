"""Run the advanced service-style tasgi example app."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from tasgi import run

from app import app


def main() -> None:
    """Run the example app on the default host and port."""

    print("Advanced tasgi service API example with the native HTTP/2 prototype.")
    print("Overview: curl --http2-prior-knowledge http://127.0.0.1:8000/")
    print("Catalog: curl --http2-prior-knowledge http://127.0.0.1:8000/api/catalog/products")
    print("Session: curl --http2-prior-knowledge -H 'Authorization: Bearer demo-token' http://127.0.0.1:8000/me")
    print("Create order: curl --http2-prior-knowledge -H 'Authorization: Bearer demo-token' -H 'Content-Type: application/json' -X POST http://127.0.0.1:8000/api/orders -d '{\"items\":[{\"sku\":\"sku-laptop-14\",\"quantity\":1}]}'")
    print("Metrics: curl --http2-prior-knowledge -H 'Authorization: Bearer ops-token' http://127.0.0.1:8000/api/ops/metrics")
    print("Admin job: curl --http2-prior-knowledge -H 'Authorization: Bearer admin-token' -X POST http://127.0.0.1:8000/api/ops/jobs/rebuild-search-index")
    print("OpenAPI JSON: http://127.0.0.1:8000/openapi.json")
    print("Swagger UI: http://127.0.0.1:8000/docs")
    print("WebSocket demo endpoint: ws://127.0.0.1:8000/ws/notifications")
    run(app)


if __name__ == "__main__":
    main()
