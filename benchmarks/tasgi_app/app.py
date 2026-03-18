"""tasgi benchmark app with endpoints matching the FastAPI benchmark app."""

from __future__ import annotations

from tasgi import ASYNC_EXECUTION, TasgiApp, TasgiConfig, THREAD_EXECUTION

from benchmarks.shared.config import BenchmarkConfig, load_benchmark_config
from benchmarks.shared.workload import (
    BenchmarkMetrics,
    cpu_payload,
    json_payload,
    root_text,
    sleep_payload,
    sleep_work,
)


def build_app(config: BenchmarkConfig | None = None) -> TasgiApp:
    """Create the tasgi benchmark app."""

    resolved = config or load_benchmark_config()
    app = TasgiApp(
        host=resolved.host,
        port=resolved.port,
        debug=False,
        default_execution=THREAD_EXECUTION,
        thread_pool_workers=resolved.thread_workers,
    )
    metrics = BenchmarkMetrics()

    @app.on_startup
    def startup(app_instance) -> None:
        app_instance.add_service("bench_metrics", metrics)

    @app.on_shutdown
    def shutdown(app_instance) -> None:
        app_instance.remove_service("bench_metrics")

    @app.route.get("/", execution=ASYNC_EXECUTION)
    async def root_route(request) -> str:
        request.service("bench_metrics").record("root")
        return root_text()

    @app.route.get("/json", execution=ASYNC_EXECUTION)
    async def json_route(request) -> dict[str, object]:
        request.service("bench_metrics").record("json")
        return json_payload()

    @app.route.post("/echo", execution=ASYNC_EXECUTION)
    async def echo_route(request) -> bytes:
        request.service("bench_metrics").record("echo")
        return request.body

    @app.route.get("/sleep")
    def sleep_route(request) -> dict[str, object]:
        payload = sleep_payload(sleep_work(resolved.sleep_seconds))
        request.service("bench_metrics").record("sleep")
        return payload

    @app.route.get("/cpu")
    def cpu_route(request) -> dict[str, object]:
        payload = cpu_payload(resolved.cpu_iterations)
        request.service("bench_metrics").record("cpu")
        return payload

    @app.route.post("/__bench/reset", include_in_schema=False)
    async def reset_metrics(request) -> dict[str, bool]:
        request.service("bench_metrics").reset()
        return {"ok": True}

    @app.route.get("/__bench/metrics", include_in_schema=False)
    async def metrics_route(request) -> dict[str, dict[str, object]]:
        return request.service("bench_metrics").snapshot()

    return app
