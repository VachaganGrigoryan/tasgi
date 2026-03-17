"""Benchmark application and metrics helpers for tasgi."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from tasgi import JsonResponse, TasgiApp, TasgiConfig, TextResponse


def cpu_demo_work(iterations: int) -> int:
    """Run a deterministic CPU-heavy workload for benchmarking."""

    total = 0
    for index in range(iterations):
        total += (index * index) % 97
    return total


@dataclass
class BenchmarkMetrics:
    """Thread-safe metrics collected during benchmark runs."""

    _hits: dict[str, int] = field(default_factory=dict)
    _thread_ids: dict[str, set[int]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def reset(self) -> None:
        """Clear all collected benchmark metrics."""

        with self._lock:
            self._hits.clear()
            self._thread_ids.clear()

    def record(self, label: str) -> None:
        """Record one benchmark event and the executing thread id."""

        thread_id = threading.get_ident()
        with self._lock:
            self._hits[label] = self._hits.get(label, 0) + 1
            self._thread_ids.setdefault(label, set()).add(thread_id)

    def snapshot(self) -> dict[str, dict[str, object]]:
        """Return a snapshot of counts and thread usage."""

        with self._lock:
            return {
                label: {
                    "hits": self._hits.get(label, 0),
                    "thread_ids": sorted(thread_ids),
                }
                for label, thread_ids in self._thread_ids.items()
            }

    def thread_count(self, label: str) -> int:
        """Return the number of distinct threads used for one label."""

        with self._lock:
            return len(self._thread_ids.get(label, set()))


@dataclass(frozen=True)
class BenchmarkHarness:
    """Bundle the benchmark app with the shared metrics service."""

    app: TasgiApp
    metrics: BenchmarkMetrics


def build_benchmark_harness(
    *,
    thread_pool_workers: int = 8,
    blocking_seconds: float = 0.01,
    cpu_iterations: int = 300_000,
) -> BenchmarkHarness:
    """Create a tasgi app instrumented for benchmark and validation runs."""

    config = TasgiConfig(
        host="127.0.0.1",
        port=8000,
        debug=False,
        default_execution="async",
        thread_pool_workers=thread_pool_workers,
    )
    app = TasgiApp(config=config)
    metrics = BenchmarkMetrics()

    @app.on_startup
    def startup(app_instance) -> None:
        app_instance.add_service("bench_metrics", metrics)

    @app.on_shutdown
    def shutdown(app_instance) -> None:
        app_instance.remove_service("bench_metrics")

    @app.get("/async")
    async def async_route(request) -> JsonResponse:
        request.service("bench_metrics").record("async")
        return JsonResponse({"mode": "async"})

    @app.get("/thread")
    def thread_route(request) -> JsonResponse:
        request.service("bench_metrics").record("thread")
        return JsonResponse({"mode": "thread"})

    @app.get("/thread-blocking")
    def thread_blocking_route(request) -> TextResponse:
        metrics_service = request.service("bench_metrics")
        metrics_service.record("thread-blocking")
        time.sleep(blocking_seconds)
        return TextResponse("thread blocking")

    @app.get("/cpu")
    def cpu_route(request) -> JsonResponse:
        metrics_service = request.service("bench_metrics")
        metrics_service.record("cpu")
        return JsonResponse({"mode": "cpu", "value": cpu_demo_work(cpu_iterations)})

    return BenchmarkHarness(app=app, metrics=metrics)
