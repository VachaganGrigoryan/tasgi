"""Run the FastAPI benchmark app."""

from __future__ import annotations

import uvicorn

from benchmarks.fastapi_app.app import build_app
from benchmarks.shared.config import load_benchmark_config
from benchmarks.shared.runtime_info import runtime_summary


def main() -> None:
    config = load_benchmark_config()
    app = build_app(config)
    print(runtime_summary(config.host, config.port, "fastapi"), flush=True)
    uvicorn.run(app, host=config.host, port=config.port, log_level="warning")


if __name__ == "__main__":
    main()
