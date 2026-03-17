"""Run loopback benchmarks against tasgi over real TCP sockets."""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from tasgi import ASGIServer

from benchmark_app import BenchmarkHarness, build_benchmark_harness


@dataclass(frozen=True)
class BenchmarkScenario:
    """One benchmark scenario definition."""

    name: str
    path: str
    metric_label: str
    requests: int
    concurrency: int
    expect_multiple_threads: bool = False


@dataclass(frozen=True)
class BenchmarkResult:
    """Summary produced by one benchmark scenario."""

    name: str
    path: str
    requests: int
    concurrency: int
    total_seconds: float
    avg_latency_ms: float
    p95_latency_ms: float
    requests_per_second: float
    worker_threads_used: int


def build_parser() -> argparse.ArgumentParser:
    """Create the benchmark CLI parser."""

    parser = argparse.ArgumentParser(description="Run tasgi loopback benchmarks.")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind.")
    parser.add_argument("--port", type=int, default=0, help="Port to bind. Use 0 for an ephemeral port.")
    parser.add_argument(
        "--requests",
        type=int,
        default=300,
        help="Requests for the async and thread baseline scenarios.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=50,
        help="Concurrency for the async and thread baseline scenarios.",
    )
    parser.add_argument(
        "--cpu-requests",
        type=int,
        default=32,
        help="Requests for the CPU-heavy threaded scenario.",
    )
    parser.add_argument(
        "--cpu-concurrency",
        type=int,
        default=8,
        help="Concurrency for the CPU-heavy threaded scenario.",
    )
    parser.add_argument(
        "--thread-workers",
        type=int,
        default=8,
        help="Thread pool size for threaded benchmark routes.",
    )
    parser.add_argument(
        "--blocking-seconds",
        type=float,
        default=0.01,
        help="Sleep duration used by the blocking threaded validation route.",
    )
    parser.add_argument(
        "--cpu-iterations",
        type=int,
        default=300_000,
        help="Iterations used by the CPU-heavy threaded route.",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=10,
        help="Warmup requests sent to each benchmark route before timing starts.",
    )
    return parser


async def main_async(args: argparse.Namespace) -> int:
    """Run all benchmark scenarios and print a summary."""

    harness = build_benchmark_harness(
        thread_pool_workers=args.thread_workers,
        blocking_seconds=args.blocking_seconds,
        cpu_iterations=args.cpu_iterations,
    )
    scenarios = [
        BenchmarkScenario(
            name="async-baseline",
            path="/async",
            metric_label="async",
            requests=args.requests,
            concurrency=args.concurrency,
        ),
        BenchmarkScenario(
            name="thread-baseline",
            path="/thread",
            metric_label="thread",
            requests=args.requests,
            concurrency=args.concurrency,
        ),
        BenchmarkScenario(
            name="thread-blocking",
            path="/thread-blocking",
            metric_label="thread-blocking",
            requests=max(args.thread_workers * 2, args.concurrency),
            concurrency=max(2, min(args.concurrency, args.thread_workers * 2)),
            expect_multiple_threads=True,
        ),
        BenchmarkScenario(
            name="cpu-threaded",
            path="/cpu",
            metric_label="cpu",
            requests=args.cpu_requests,
            concurrency=args.cpu_concurrency,
            expect_multiple_threads=True,
        ),
    ]

    results = await _run_suite(
        harness,
        host=args.host,
        port=args.port,
        scenarios=scenarios,
        warmup=args.warmup,
    )
    _print_results(results)
    _print_comparison(results)
    return 0


def main() -> None:
    """CLI entrypoint for the benchmark suite."""

    args = build_parser().parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


async def _run_suite(
    harness: BenchmarkHarness,
    *,
    host: str,
    port: int,
    scenarios: list[BenchmarkScenario],
    warmup: int,
) -> list[BenchmarkResult]:
    server = ASGIServer(harness.app)

    async with harness.app.lifespan():
        listener = await asyncio.start_server(server.handle_connection, host, port)
        try:
            sockets = listener.sockets or []
            if not sockets:
                raise RuntimeError("Benchmark server did not open a listening socket.")
            bound_port = int(sockets[0].getsockname()[1])

            results: list[BenchmarkResult] = []
            for scenario in scenarios:
                harness.metrics.reset()
                await _warmup(host, bound_port, scenario.path, warmup)
                harness.metrics.reset()
                result = await _run_scenario(harness, host, bound_port, scenario)
                results.append(result)
            return results
        finally:
            listener.close()
            await listener.wait_closed()


async def _warmup(host: str, port: int, path: str, count: int) -> None:
    if count <= 0:
        return
    for _ in range(count):
        await _issue_request(host, port, path)


async def _run_scenario(
    harness: BenchmarkHarness,
    host: str,
    port: int,
    scenario: BenchmarkScenario,
) -> BenchmarkResult:
    latencies: list[float] = []
    semaphore = asyncio.Semaphore(scenario.concurrency)

    async def one_request() -> None:
        async with semaphore:
            started = perf_counter()
            await _issue_request(host, port, scenario.path)
            latencies.append((perf_counter() - started) * 1000.0)

    started = perf_counter()
    await asyncio.gather(*[one_request() for _ in range(scenario.requests)])
    total_seconds = perf_counter() - started

    worker_threads_used = harness.metrics.thread_count(scenario.metric_label)
    if scenario.expect_multiple_threads and worker_threads_used < 2:
        raise RuntimeError(
            "Scenario %s did not use multiple worker threads." % scenario.name
        )

    return BenchmarkResult(
        name=scenario.name,
        path=scenario.path,
        requests=scenario.requests,
        concurrency=scenario.concurrency,
        total_seconds=total_seconds,
        avg_latency_ms=statistics.fmean(latencies),
        p95_latency_ms=_percentile(latencies, 0.95),
        requests_per_second=scenario.requests / total_seconds,
        worker_threads_used=worker_threads_used,
    )


async def _issue_request(host: str, port: int, path: str) -> bytes:
    reader, writer = await asyncio.open_connection(host, port)
    request = (
        "GET %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n" % (path, host)
    ).encode("ascii")
    writer.write(request)
    await writer.drain()

    response = bytearray()
    try:
        while True:
            chunk = await reader.read(65_536)
            if not chunk:
                break
            response.extend(chunk)
    finally:
        writer.close()
        await writer.wait_closed()

    response_bytes = bytes(response)
    status_line = response_bytes.split(b"\r\n", maxsplit=1)[0]
    if b" 200 " not in status_line:
        raise RuntimeError("Benchmark request failed: %s" % status_line.decode("latin-1"))
    return response_bytes


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


def _print_results(results: list[BenchmarkResult]) -> None:
    print(
        "scenario           requests  conc  total(s)  req/s    avg(ms)  p95(ms)  worker_threads"
    )
    for result in results:
        print(
            "%-17s %8d %5d %8.3f %7.1f %8.3f %8.3f %15d"
            % (
                result.name,
                result.requests,
                result.concurrency,
                result.total_seconds,
                result.requests_per_second,
                result.avg_latency_ms,
                result.p95_latency_ms,
                result.worker_threads_used,
            )
        )


def _print_comparison(results: list[BenchmarkResult]) -> None:
    async_result = _find_result(results, "async-baseline")
    thread_result = _find_result(results, "thread-baseline")
    cpu_result = _find_result(results, "cpu-threaded")

    if async_result is not None and thread_result is not None:
        throughput_ratio = thread_result.requests_per_second / async_result.requests_per_second
        latency_ratio = thread_result.avg_latency_ms / async_result.avg_latency_ms
        print("")
        print(
            "async vs thread baseline: throughput x%.2f, avg latency x%.2f"
            % (throughput_ratio, latency_ratio)
        )

    if cpu_result is not None:
        print(
            "cpu-threaded validation: %d worker threads observed during CPU concurrency"
            % cpu_result.worker_threads_used
        )


def _find_result(results: list[BenchmarkResult], name: str) -> Optional[BenchmarkResult]:
    for result in results:
        if result.name == name:
            return result
    return None


if __name__ == "__main__":
    main()
