"""Run the example tasgi demo app."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from tasgi import run

from app import app


def main() -> None:
    """Run the example app on the default host and port."""

    print("Demo app is configured to require HTTP/2.")
    print("Use: curl --http2-prior-knowledge http://127.0.0.1:8000/")
    run(app)


if __name__ == "__main__":
    main()
