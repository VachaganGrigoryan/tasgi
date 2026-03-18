"""Run apples-to-apples loopback benchmarks for tasgi and FastAPI."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import signal
import statistics
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
import shutil
from time import perf_counter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.shared.config import BenchmarkConfig, load_benchmark_config


@dataclass(frozen=True)
class BenchmarkScenario:
    """One benchmark scenario definition."""

    name: str
    method: str
    path: str
    metric_label: str
    requests: int
    concurrency: int
    body: bytes = b""
    expect_multiple_threads: bool = False


@dataclass(frozen=True)
class BenchmarkResult:
    """Summary produced by one benchmark scenario."""

    framework: str
    name: str
    path: str
    requests: int
    concurrency: int
    total_seconds: float
    avg_latency_ms: float
    p95_latency_ms: float
    requests_per_second: float
    worker_threads_used: int


@dataclass(frozen=True)
class ServerTarget:
    """One benchmark server target."""

    name: str
    command: list[str]


async def main_async() -> int:
    """Run all benchmark scenarios and print a summary."""

    config = load_benchmark_config()
    echo_body = b'{"message":"hello"}'
    scenarios = [
        BenchmarkScenario(
            name="root",
            method="GET",
            path="/",
            metric_label="root",
            requests=config.requests,
            concurrency=config.concurrency,
        ),
        BenchmarkScenario(
            name="json",
            method="GET",
            path="/json",
            metric_label="json",
            requests=config.requests,
            concurrency=config.concurrency,
        ),
        BenchmarkScenario(
            name="echo",
            method="POST",
            path="/echo",
            metric_label="echo",
            requests=config.requests,
            concurrency=config.concurrency,
            body=echo_body,
        ),
        BenchmarkScenario(
            name="sleep",
            method="GET",
            path="/sleep",
            metric_label="sleep",
            requests=config.requests,
            concurrency=config.concurrency,
            expect_multiple_threads=True,
        ),
        BenchmarkScenario(
            name="cpu",
            method="GET",
            path="/cpu",
            metric_label="cpu",
            requests=config.cpu_requests,
            concurrency=config.cpu_concurrency,
            expect_multiple_threads=True,
        ),
    ]
    targets = [
        ServerTarget(
            name="tasgi",
            command=[sys.executable, "-m", "benchmarks.tasgi_app.main"],
        ),
        ServerTarget(
            name="fastapi",
            command=[sys.executable, "-m", "benchmarks.fastapi_app.main"],
        ),
    ]

    results: list[BenchmarkResult] = []
    for target in targets:
        results.extend(await _run_target(target, config, scenarios))

    _print_results(results)
    _print_comparison(results, [scenario.name for scenario in scenarios])
    return 0


def main() -> None:
    """CLI entrypoint for the benchmark suite."""

    _validate_benchmark_dependencies(load_benchmark_config())
    raise SystemExit(asyncio.run(main_async()))


async def _run_target(
    target: ServerTarget,
    config: BenchmarkConfig,
    scenarios: list[BenchmarkScenario],
) -> list[BenchmarkResult]:
    process = await _start_server(target, config)
    try:
        await _wait_until_ready(target, process, config.host, config.port)

        results: list[BenchmarkResult] = []
        for scenario in scenarios:
            await _reset_metrics(config.host, config.port)
            await _warmup(config.host, config.port, scenario, config.warmup)
            await _reset_metrics(config.host, config.port)
            results.append(await _run_scenario(target.name, config, scenario))
        return results
    finally:
        await _stop_server(process)


async def _warmup(host: str, port: int, scenario: BenchmarkScenario, count: int) -> None:
    if count <= 0:
        return
    for _ in range(count):
        await _issue_request(host, port, scenario.method, scenario.path, scenario.body)


async def _run_scenario(
    framework: str,
    config: BenchmarkConfig,
    scenario: BenchmarkScenario,
) -> BenchmarkResult:
    if config.client == "ab":
        return await _run_scenario_with_ab(framework, config, scenario)

    latencies: list[float] = []
    semaphore = asyncio.Semaphore(scenario.concurrency)

    async def one_request() -> None:
        async with semaphore:
            started = perf_counter()
            await _issue_request(
                config.host,
                config.port,
                scenario.method,
                scenario.path,
                scenario.body,
            )
            latencies.append((perf_counter() - started) * 1000.0)

    started = perf_counter()
    await asyncio.gather(*[one_request() for _ in range(scenario.requests)])
    total_seconds = perf_counter() - started

    metrics = await _fetch_metrics(config.host, config.port)
    worker_threads_used = len(metrics.get(scenario.metric_label, {}).get("thread_ids", []))
    if scenario.expect_multiple_threads and worker_threads_used < 2:
        raise RuntimeError(
            "Scenario %s for %s did not use multiple worker threads."
            % (scenario.name, framework)
        )

    return BenchmarkResult(
        framework=framework,
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


async def _run_scenario_with_ab(
    framework: str,
    config: BenchmarkConfig,
    scenario: BenchmarkScenario,
) -> BenchmarkResult:
    command = [
        config.ab_path,
        "-n",
        str(scenario.requests),
        "-c",
        str(scenario.concurrency),
    ]

    temp_path: str | None = None
    if scenario.method == "POST":
        handle = tempfile.NamedTemporaryFile(delete=False)
        handle.write(scenario.body)
        handle.flush()
        handle.close()
        temp_path = handle.name
        command.extend(["-p", temp_path, "-T", "application/json"])

    command.append("http://%s:%d%s" % (config.host, config.port, scenario.path))
    print(
        "[%s] ab -n %s -c %s %s"
        % (framework, scenario.requests, scenario.concurrency, command[-1])
    )

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(PROJECT_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output_bytes, _ = await process.communicate()
        output = output_bytes.decode("utf-8", errors="replace")
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass

    if process.returncode != 0:
        raise RuntimeError("ab failed for %s on %s.\n%s" % (framework, scenario.name, output))

    parsed = _parse_ab_output(output)
    metrics = await _fetch_metrics(config.host, config.port)
    worker_threads_used = len(metrics.get(scenario.metric_label, {}).get("thread_ids", []))
    if scenario.expect_multiple_threads and worker_threads_used < 2:
        raise RuntimeError(
            "Scenario %s for %s did not use multiple worker threads."
            % (scenario.name, framework)
        )

    return BenchmarkResult(
        framework=framework,
        name=scenario.name,
        path=scenario.path,
        requests=scenario.requests,
        concurrency=scenario.concurrency,
        total_seconds=parsed["total_seconds"],
        avg_latency_ms=parsed["avg_latency_ms"],
        p95_latency_ms=parsed["p95_latency_ms"],
        requests_per_second=parsed["requests_per_second"],
        worker_threads_used=worker_threads_used,
    )


async def _issue_request(
    host: str,
    port: int,
    method: str,
    path: str,
    body: bytes = b"",
) -> bytes:
    reader, writer = await asyncio.open_connection(host, port)
    writer.write(_build_http_request(method, host, path, body))
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
    status = int(response_bytes.split(b" ", 2)[1])
    if status >= 400:
        raise RuntimeError("Request %s %s failed with HTTP %s." % (method, path, status))
    return response_bytes


def _build_http_request(method: str, host: str, path: str, body: bytes) -> bytes:
    headers = [
        f"{method} {path} HTTP/1.1",
        f"Host: {host}",
        "Connection: close",
    ]
    if body:
        headers.append("Content-Type: application/json")
        headers.append(f"Content-Length: {len(body)}")
    return ("\r\n".join(headers) + "\r\n\r\n").encode("ascii") + body


def _validate_benchmark_dependencies(config: BenchmarkConfig) -> None:
    missing: list[str] = []
    for module_name in ["tasgi", "fastapi", "uvicorn"]:
        if importlib.util.find_spec(module_name) is None:
            missing.append(module_name)

    if config.client == "ab" and shutil.which(config.ab_path) is None:
        missing.append("ab")

    if not missing:
        return

    package_hint = ", ".join(missing)
    raise RuntimeError(
        "Missing benchmark dependencies: %s.\n"
        "Install them in the benchmark environment first.\n"
        "If you are benchmarking the local repo before publishing:\n"
        "  python3 -m pip install -r requirements-local.txt\n"
        "If tasgi is published on PyPI:\n"
        "  python3 -m pip install -r requirements-pypi.txt"
        % package_hint
    )


def _parse_ab_output(output: str) -> dict[str, float]:
    failed_requests = _match_ab_value(output, r"Failed requests:\s+(\d+)")
    if int(failed_requests) != 0:
        raise RuntimeError("ab reported failed requests.\n%s" % output)

    total_seconds = _match_ab_value(output, r"Time taken for tests:\s+([0-9.]+)\s+seconds")
    requests_per_second = _match_ab_value(output, r"Requests per second:\s+([0-9.]+)\s+\[#/sec\]")
    avg_latency_ms = _match_ab_value(output, r"Time per request:\s+([0-9.]+)\s+\[ms\]\s+\(mean\)")
    p95_latency_ms = _match_ab_value(
        output,
        r"^\s*95%\s+([0-9.]+)\s*$",
        flags=re.MULTILINE,
    )
    return {
        "total_seconds": total_seconds,
        "requests_per_second": requests_per_second,
        "avg_latency_ms": avg_latency_ms,
        "p95_latency_ms": p95_latency_ms,
    }


def _match_ab_value(output: str, pattern: str, *, flags: int = 0) -> float:
    match = re.search(pattern, output, flags)
    if match is None:
        raise RuntimeError("Could not parse ab output.\n%s" % output)
    return float(match.group(1))


async def _start_server(
    target: ServerTarget,
    config: BenchmarkConfig,
) -> asyncio.subprocess.Process:
    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(
        [value for value in [str(PROJECT_ROOT), existing_pythonpath] if value]
    )
    env["BENCHMARK_HOST"] = config.host
    env["BENCHMARK_PORT"] = str(config.port)
    env["BENCHMARK_REQUESTS"] = str(config.requests)
    env["BENCHMARK_CONCURRENCY"] = str(config.concurrency)
    env["BENCHMARK_CPU_ITERATIONS"] = str(config.cpu_iterations)
    env["BENCHMARK_SLEEP_SECONDS"] = str(config.sleep_seconds)
    env["BENCHMARK_WARMUP"] = str(config.warmup)
    env["BENCHMARK_THREAD_WORKERS"] = str(config.thread_workers)
    env["BENCHMARK_TEST_MODE"] = config.test_mode

    return await asyncio.create_subprocess_exec(
        *target.command,
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )


async def _stop_server(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


async def _wait_until_ready(
    target: ServerTarget,
    process: asyncio.subprocess.Process,
    host: str,
    port: int,
    timeout: float = 10.0,
) -> None:
    startup_output = ""
    deadline = perf_counter() + timeout
    while perf_counter() < deadline:
        if process.returncode is not None:
            output = startup_output + await _read_process_output(process)
            raise RuntimeError(
                "%s server exited before startup.\n%s"
                % (target.name, output or "No process output was captured.")
            )
        try:
            await _issue_request(host, port, "GET", "/")
            if startup_output.strip():
                print(startup_output.strip())
            return
        except Exception:
            startup_output += await _drain_process_output(process)
            await asyncio.sleep(0.1)
    raise RuntimeError(
        "%s server did not start listening on time.\n%s"
        % (target.name, startup_output.strip() or "No process output was captured.")
    )


async def _read_process_output(process: asyncio.subprocess.Process) -> str:
    if process.stdout is None:
        return ""
    output = await process.stdout.read()
    return output.decode("utf-8", errors="replace").strip()


async def _drain_process_output(process: asyncio.subprocess.Process) -> str:
    if process.stdout is None:
        return ""

    chunks: list[str] = []
    while True:
        try:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=0.01)
        except asyncio.TimeoutError:
            break
        if not line:
            break
        chunks.append(line.decode("utf-8", errors="replace"))
        if process.stdout.at_eof():
            break
    return "".join(chunks)


async def _reset_metrics(host: str, port: int) -> None:
    await _issue_request(host, port, "POST", "/__bench/reset")


async def _fetch_metrics(host: str, port: int) -> dict[str, dict[str, object]]:
    response = await _issue_request(host, port, "GET", "/__bench/metrics")
    body = response.split(b"\r\n\r\n", maxsplit=1)[1]
    return json.loads(body.decode("utf-8"))


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


def _print_results(results: list[BenchmarkResult]) -> None:
    print("\nBenchmark results:")
    print(
        "  %-10s %-8s %10s %12s %12s %12s %8s"
        % ("App", "Scenario", "Req/s", "Avg ms", "P95 ms", "Total s", "Threads")
    )
    for result in results:
        print(
            "  %-10s %-8s %10.1f %12.2f %12.2f %12.3f %8d"
            % (
                result.framework,
                result.name,
                result.requests_per_second,
                result.avg_latency_ms,
                result.p95_latency_ms,
                result.total_seconds,
                result.worker_threads_used,
            )
        )


def _print_comparison(results: list[BenchmarkResult], scenario_names: list[str]) -> None:
    grouped: dict[str, dict[str, BenchmarkResult]] = {}
    for result in results:
        grouped.setdefault(result.name, {})[result.framework] = result

    print("\nComparison:")
    print("  %-8s %-10s %-10s %-10s" % ("Scenario", "tasgi", "fastapi", "Ratio"))
    for scenario_name in scenario_names:
        pair = grouped.get(scenario_name, {})
        tasgi_result = pair.get("tasgi")
        fastapi_result = pair.get("fastapi")
        if tasgi_result is None or fastapi_result is None:
            continue
        ratio = tasgi_result.requests_per_second / fastapi_result.requests_per_second
        print(
            "  %-8s %-10.1f %-10.1f %-10.2fx"
            % (
                scenario_name,
                tasgi_result.requests_per_second,
                fastapi_result.requests_per_second,
                ratio,
            )
        )


if __name__ == "__main__":
    main()
