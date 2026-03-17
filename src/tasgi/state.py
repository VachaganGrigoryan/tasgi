"""Thread-safe application state container."""

from __future__ import annotations

import threading
from typing import Any


class AppState:
    """Small thread-safe state container for app-wide services and values."""

    def __init__(self) -> None:
        object.__setattr__(self, "_data", {})
        object.__setattr__(self, "_lock", threading.RLock())

    def __getattr__(self, name: str) -> Any:
        with self._lock:
            if name not in self._data:
                raise AttributeError(name)
            return self._data[name]

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        with self._lock:
            self._data[name] = value

    def __delattr__(self, name: str) -> None:
        with self._lock:
            if name not in self._data:
                raise AttributeError(name)
            del self._data[name]

    def get(self, name: str, default: Any = None) -> Any:
        """Return a stored value or a default when the key is absent."""

        with self._lock:
            return self._data.get(name, default)

    def snapshot(self) -> dict[str, Any]:
        """Return a shallow copy of the stored state."""

        with self._lock:
            return dict(self._data)
