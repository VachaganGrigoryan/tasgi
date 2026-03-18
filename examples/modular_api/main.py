"""Run the modular tasgi example app."""

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
    print("Modular tasgi API example.")
    print("Overview: http://127.0.0.1:8010/")
    print("Docs: http://127.0.0.1:8010/docs")
    print("List tasks: curl -H 'Authorization: Bearer demo-token' http://127.0.0.1:8010/api/tasks")
    print("Create task: curl -H 'Authorization: Bearer writer-token' -H 'Content-Type: application/json' -X POST http://127.0.0.1:8010/api/tasks -d '{\"title\":\"Ship weekly report\",\"owner\":\"ops\"}'")
    print("Admin stats: curl -H 'Authorization: Bearer admin-token' http://127.0.0.1:8010/api/admin/stats")
    run(app)


if __name__ == "__main__":
    main()

