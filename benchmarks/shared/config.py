"""Shared benchmark configuration loaded from benchmarks/.env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


@dataclass(frozen=True)
class BenchmarkConfig:
    """Common configuration shared by both benchmark apps and the runner."""

    host: str = "127.0.0.1"
    port: int = 9000
    requests: int = 300
    concurrency: int = 50
    cpu_requests: int = 1_000
    cpu_concurrency: int = 12
    cpu_iterations: int = 320_000
    sleep_seconds: float = 0.01
    warmup: int = 10
    thread_workers: int = 12
    test_mode: str = "full"
    client: str = "ab"
    ab_path: str = "ab"


def load_benchmark_config(env_path: Path | None = None) -> BenchmarkConfig:
    """Load benchmark settings from a simple .env file plus process overrides."""

    values = _parse_env_file(env_path or DEFAULT_ENV_PATH)
    values.update({key: value for key, value in os.environ.items() if key.startswith("BENCHMARK_")})

    config = BenchmarkConfig(
        host=values.get("BENCHMARK_HOST", "127.0.0.1"),
        port=int(values.get("BENCHMARK_PORT", "9000")),
        requests=int(values.get("BENCHMARK_REQUESTS", "300")),
        concurrency=int(values.get("BENCHMARK_CONCURRENCY", "50")),
        cpu_requests=int(values.get("BENCHMARK_CPU_REQUESTS", "1000")),
        cpu_concurrency=int(values.get("BENCHMARK_CPU_CONCURRENCY", "12")),
        cpu_iterations=int(values.get("BENCHMARK_CPU_ITERATIONS", "320000")),
        sleep_seconds=float(values.get("BENCHMARK_SLEEP_SECONDS", "0.01")),
        warmup=int(values.get("BENCHMARK_WARMUP", "10")),
        thread_workers=int(values.get("BENCHMARK_THREAD_WORKERS", "12")),
        test_mode=values.get("BENCHMARK_TEST_MODE", "full").strip().lower() or "full",
        client=values.get("BENCHMARK_CLIENT", "ab").strip().lower() or "ab",
        ab_path=values.get("BENCHMARK_AB_PATH", "ab").strip() or "ab",
    )
    if config.test_mode == "smoke":
        return BenchmarkConfig(
            host=config.host,
            port=config.port,
            requests=min(config.requests, 40),
            concurrency=min(config.concurrency, 8),
            cpu_requests=min(config.cpu_requests, 80),
            cpu_concurrency=min(config.cpu_concurrency, 8),
            cpu_iterations=min(config.cpu_iterations, 50_000),
            sleep_seconds=config.sleep_seconds,
            warmup=min(config.warmup, 3),
            thread_workers=config.thread_workers,
            test_mode=config.test_mode,
            client=config.client,
            ab_path=config.ab_path,
        )
    return config


def _parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values
