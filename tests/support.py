"""Shared test helpers for tasgi's unittest suite."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
SERVICE_API_ROOT = PROJECT_ROOT / "examples" / "service_api"
MODULAR_API_ROOT = PROJECT_ROOT / "examples" / "modular_api"

for candidate in [str(SRC_ROOT), str(SERVICE_API_ROOT), str(MODULAR_API_ROOT)]:
    if candidate not in sys.path:
        sys.path.insert(0, candidate)


def build_get_request(path: str) -> bytes:
    return f"GET {path} HTTP/1.1\r\nHost: example.test\r\n\r\n".encode("ascii")


def build_post_request(path: str, body: bytes) -> bytes:
    return (
        f"POST {path} HTTP/1.1\r\nHost: example.test\r\nContent-Length: {len(body)}\r\nContent-Type: application/json\r\n\r\n".encode(
            "ascii"
        )
        + body
    )


def with_header(request: bytes, name: str, value: str) -> bytes:
    return request.replace(
        b"\r\n\r\n",
        f"\r\n{name}: {value}\r\n\r\n".encode("latin-1"),
        1,
    )


def load_example_module(module_name: str, root: Path):
    """Load an example app module while isolating sibling imports."""

    root_str = str(root)
    for candidate in [str(SERVICE_API_ROOT), str(MODULAR_API_ROOT)]:
        while candidate in sys.path:
            sys.path.remove(candidate)
    if str(SRC_ROOT) in sys.path:
        sys.path.remove(str(SRC_ROOT))
    sys.path.insert(0, str(SRC_ROOT))
    sys.path.insert(0, root_str)

    for candidate in [
        "models",
        "services",
        "routers",
        "routers.public",
        "routers.tasks",
        "routers.admin",
        module_name,
    ]:
        sys.modules.pop(candidate, None)

    spec = importlib.util.spec_from_file_location(module_name, root / "app.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load example app module.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cpu_demo_work(iterations: int = 60_000) -> int:
    """Mirror the deterministic CPU demo workload used in framework tests."""

    total = 0
    for index in range(iterations):
        total += (index * index) % 97
    return total
