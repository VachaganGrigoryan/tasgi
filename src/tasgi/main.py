"""Runtime entry points for tasgi."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
from typing import Optional
from pathlib import Path

from .asgi_server import ASGIServer
from .config import TasgiConfig
from .types import ASGIApp


def build_parser() -> argparse.ArgumentParser:
    """Create the tasgi CLI parser."""

    parser = argparse.ArgumentParser(description="Run the tasgi demo server.")
    parser.add_argument("--host", help="Host interface to bind.")
    parser.add_argument("--port", type=int, help="TCP port to bind.")
    return parser


async def serve(
    app: ASGIApp,
    host: Optional[str] = None,
    port: Optional[int] = None,
) -> None:
    """Serve an ASGI app using explicit values or the app config defaults."""

    server = ASGIServer(app)
    config = getattr(app, "config", TasgiConfig())
    resolved_host = host or config.host
    resolved_port = port or config.port
    await server.serve(host=resolved_host, port=resolved_port)


def run(
    app: ASGIApp,
    host: Optional[str] = None,
    port: Optional[int] = None,
) -> None:
    """Run an ASGI app until interrupted."""

    config = getattr(app, "config", TasgiConfig())
    resolved_host = host or config.host
    resolved_port = port or config.port
    print("Starting tasgi on http://%s:%s" % (resolved_host, resolved_port))
    try:
        asyncio.run(serve(app, host=resolved_host, port=resolved_port))
    except KeyboardInterrupt:
        print("Server stopped.")


def main() -> None:
    """Run the bundled tasgi demo application."""

    args = build_parser().parse_args()
    run(_load_repo_demo_app(), host=args.host, port=args.port)


def _load_repo_demo_app() -> ASGIApp:
    """Load the repository demo app from ``examples/demo_app/app.py``."""

    project_root = Path(__file__).resolve().parents[2]
    demo_app_path = project_root / "examples" / "demo_app" / "app.py"
    spec = importlib.util.spec_from_file_location("tasgi_repo_demo_app", demo_app_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load demo app from %s." % demo_app_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.app


if __name__ == "__main__":
    main()
