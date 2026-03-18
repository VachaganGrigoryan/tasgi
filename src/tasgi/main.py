"""Runtime entry points for tasgi."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
from pathlib import Path
import sys
from typing import Optional

from .asgi_server import ASGIServer
from .config import TasgiConfig
from .types import ASGIApp


def build_parser() -> argparse.ArgumentParser:
    """Create the tasgi CLI parser."""

    parser = argparse.ArgumentParser(description="Run the bundled tasgi example server.")
    parser.add_argument("--host", help="Host interface to bind.")
    parser.add_argument("--port", type=int, help="TCP port to bind.")
    return parser


async def serve(
    app: ASGIApp,
    host: Optional[str] = None,
    port: Optional[int] = None,
) -> None:
    """Serve an ASGI app using explicit values or the app config defaults."""

    config = getattr(app, "config", TasgiConfig())
    resolved_host = host or config.host
    resolved_port = port or config.port
    server = ASGIServer(app)
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
    """Run the bundled tasgi service API example application."""

    args = build_parser().parse_args()
    run(_load_repo_service_app(), host=args.host, port=args.port)


def _load_repo_service_app() -> ASGIApp:
    """Load the repository service API example app from ``examples/service_api/app.py``."""

    project_root = Path(__file__).resolve().parents[2]
    example_app_path = project_root / "examples" / "service_api" / "app.py"
    example_root = str(example_app_path.parent)
    modular_root = str(project_root / "examples" / "modular_api")
    for module_name in ["models", "services", "tasgi_repo_service_app"]:
        sys.modules.pop(module_name, None)
    for candidate in [example_root, modular_root]:
        while candidate in sys.path:
            sys.path.remove(candidate)
    sys.path.insert(0, example_root)
    spec = importlib.util.spec_from_file_location("tasgi_repo_service_app", example_app_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load example app from %s." % example_app_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.app


if __name__ == "__main__":
    main()
