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

    def set_service(self, name: str, service: Any) -> Any:
        """Register a named service on shared app state."""

        if not name:
            raise ValueError("Service name must be non-empty.")
        with self._lock:
            self._data[name] = service
        return service

    def get_service(self, name: str, default: Any = None) -> Any:
        """Return a named service or a default when it is absent."""

        return self.get(name, default)

    def require_service(self, name: str) -> Any:
        """Return a named service or raise a KeyError when missing."""

        with self._lock:
            if name not in self._data:
                raise KeyError("Service %r is not registered." % name)
            return self._data[name]

    def remove_service(self, name: str) -> Any:
        """Remove and return a named service."""

        with self._lock:
            if name not in self._data:
                raise KeyError("Service %r is not registered." % name)
            return self._data.pop(name)

    def snapshot(self) -> dict[str, Any]:
        """Return a shallow copy of the stored state."""

        with self._lock:
            return dict(self._data)
