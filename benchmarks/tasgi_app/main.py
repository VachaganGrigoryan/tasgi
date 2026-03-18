"""Run the tasgi benchmark app."""

from __future__ import annotations

import asyncio

from tasgi import serve

from benchmarks.shared.config import load_benchmark_config
from benchmarks.shared.runtime_info import runtime_summary
from benchmarks.tasgi_app.app import build_app


def main() -> None:
    config = load_benchmark_config()
    app = build_app(config)
    print(runtime_summary(config.host, config.port, "tasgi"), flush=True)
    asyncio.run(serve(app, host=config.host, port=config.port))


if __name__ == "__main__":
    main()
