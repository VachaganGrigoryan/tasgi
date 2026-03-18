"""Shared benchmark workload and thread metrics helpers."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


def cpu_demo_work(iterations: int) -> int:
    """Run the same deterministic CPU-heavy workload used by the service demo."""

    total = 0
    for index in range(iterations):
        total += ((index * index) ^ (index % 17)) % 10_007
    return total


def sleep_work(seconds: float) -> float:
    """Sleep for the configured number of seconds and return that value."""

    time.sleep(seconds)
    return seconds


def root_text() -> str:
    return "benchmark root"


def json_payload() -> dict[str, object]:
    return {"ok": True, "mode": "json"}


def sleep_payload(seconds: float) -> dict[str, object]:
    return {"ok": True, "slept": seconds}


def cpu_payload(iterations: int) -> dict[str, object]:
    return {"ok": True, "value": cpu_demo_work(iterations)}


@dataclass
class BenchmarkMetrics:
    """Thread-safe metrics collected during benchmark runs."""

    _hits: dict[str, int] = field(default_factory=dict)
    _thread_ids: dict[str, set[int]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
            self._thread_ids.clear()

    def record(self, label: str) -> None:
        thread_id = threading.get_ident()
        with self._lock:
            self._hits[label] = self._hits.get(label, 0) + 1
            self._thread_ids.setdefault(label, set()).add(thread_id)

    def snapshot(self) -> dict[str, dict[str, object]]:
        with self._lock:
            return {
                label: {
                    "hits": self._hits.get(label, 0),
                    "thread_ids": sorted(thread_ids),
                }
                for label, thread_ids in self._thread_ids.items()
            }
