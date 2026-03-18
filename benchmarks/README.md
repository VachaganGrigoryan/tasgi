# Benchmarks

This benchmark workspace is designed so it can later live outside the main `tasgi` repo.

## Install modes

### Local repo mode

Use this before `tasgi` is published, or when you want to benchmark your local checkout:

```bash
cd benchmarks
python3 -m pip install -r requirements-local.txt
```

This installs:

- benchmark-only deps from `requirements.txt`
- local editable `tasgi` from `..`

### PyPI mode

Use this after `tasgi` is published and you want the benchmark workspace to depend on the released package:

```bash
cd benchmarks
python3 -m pip install -r requirements-pypi.txt
```

This installs:

- benchmark-only deps from `requirements.txt`
- released `tasgi` from PyPI

## Run

```bash
cd benchmarks
python3 run_benchmarks.py
```

## Shared config

Settings live in `.env`:

- `BENCHMARK_HOST`
- `BENCHMARK_PORT`
- `BENCHMARK_REQUESTS`
- `BENCHMARK_CONCURRENCY`
- `BENCHMARK_CPU_REQUESTS`
- `BENCHMARK_CPU_CONCURRENCY`
- `BENCHMARK_CPU_ITERATIONS`
- `BENCHMARK_SLEEP_SECONDS`
- `BENCHMARK_WARMUP`
- `BENCHMARK_THREAD_WORKERS`
- `BENCHMARK_TEST_MODE`
- `BENCHMARK_CLIENT`
- `BENCHMARK_AB_PATH`

## Benchmark client

Two request drivers are supported:

- `BENCHMARK_CLIENT=ab`
- `BENCHMARK_CLIENT=asyncio`

`ab` is useful when you want an external tool driving the load, especially for CPU and blocking endpoint verification.

## Benchmark shape

The tasgi benchmark app runs in thread-default mode.

Light routes are explicitly kept async:

- `GET /`
- `GET /json`
- `POST /echo`

Blocking and CPU-heavy routes stay on the thread runtime:

- `GET /sleep`
- `GET /cpu`
