# Deployment

## Current recommendation

Treat `tasgi` as experimental software.

Good current use cases:

- local development
- framework experiments
- transport/runtime learning
- prototype internal services

## Run the built-in server

```python
from tasgi import run
from myapp import app

run(app)
```

Or:

```bash
tasgi
```

## Config

```python
from tasgi import TasgiApp

app = TasgiApp(
    host="127.0.0.1",
    port=8000,
    debug=False,
    default_execution="thread",
    thread_pool_workers=8,
)
```

## Important notes

- HTTP/2 support is still prototype-grade
- production-hardening is not the current project claim
- benchmark and validate your own workload before using thread-heavy routes widely

## CI and publishing

This repo includes:

- GitHub Actions CI on pushes and pull requests
- a separate publish workflow for version tags or GitHub Releases
- PyPI Trusted Publishing support
