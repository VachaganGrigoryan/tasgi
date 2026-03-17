"""Application startup and shutdown hook management."""

from __future__ import annotations

import inspect
from typing import Any, Callable

from .runtime import TasgiRuntime

LifecycleHook = Callable[[Any], Any]


class LifecycleManager:
    """Store and execute startup and shutdown hooks."""

    def __init__(self) -> None:
        self.startup_hooks: list[LifecycleHook] = []
        self.shutdown_hooks: list[LifecycleHook] = []

    def on_startup(self, hook: LifecycleHook) -> LifecycleHook:
        """Register a startup hook."""

        self.startup_hooks.append(hook)
        return hook

    def on_shutdown(self, hook: LifecycleHook) -> LifecycleHook:
        """Register a shutdown hook."""

        self.shutdown_hooks.append(hook)
        return hook

    async def run_startup(self, app: Any, runtime: TasgiRuntime) -> None:
        """Execute registered startup hooks in registration order."""

        for hook in self.startup_hooks:
            await _run_hook(hook, app, runtime)

    async def run_shutdown(self, app: Any, runtime: TasgiRuntime) -> None:
        """Execute registered shutdown hooks in reverse registration order."""

        for hook in reversed(self.shutdown_hooks):
            await _run_hook(hook, app, runtime)


async def _run_hook(hook: LifecycleHook, app: Any, runtime: TasgiRuntime) -> None:
    if inspect.iscoroutinefunction(hook):
        await hook(app)
        return
    await runtime.run_sync(hook, app)
