"""FastAPI benchmark app with endpoints matching the tasgi benchmark app."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from benchmarks.shared.config import BenchmarkConfig, load_benchmark_config
from benchmarks.shared.workload import (
    BenchmarkMetrics,
    cpu_payload,
    json_payload,
    root_text,
    sleep_payload,
    sleep_work,
)


def build_app(config: BenchmarkConfig | None = None) -> FastAPI:
    """Create the FastAPI benchmark app."""

    resolved = config or load_benchmark_config()
    metrics = BenchmarkMetrics()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.bench_metrics = metrics
        app.state.bench_config = resolved
        yield

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.get("/")
    async def root_route() -> str:
        metrics.record("root")
        return root_text()

    @app.get("/json")
    async def json_route() -> dict[str, object]:
        metrics.record("json")
        return json_payload()

    @app.post("/echo")
    async def echo_route(request: Request) -> Response:
        metrics.record("echo")
        return Response(content=await request.body(), media_type="application/octet-stream")

    @app.get("/sleep")
    def sleep_route() -> dict[str, object]:
        payload = sleep_payload(sleep_work(resolved.sleep_seconds))
        metrics.record("sleep")
        return payload

    @app.get("/cpu")
    def cpu_route() -> dict[str, object]:
        payload = cpu_payload(resolved.cpu_iterations)
        metrics.record("cpu")
        return payload

    @app.post("/__bench/reset")
    async def reset_metrics() -> dict[str, bool]:
        metrics.reset()
        return {"ok": True}

    @app.get("/__bench/metrics")
    async def metrics_route() -> dict[str, dict[str, object]]:
        return metrics.snapshot()

    return app
