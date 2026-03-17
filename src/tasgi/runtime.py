"""Execution policy and threaded runtime support."""

from __future__ import annotations

import asyncio
from concurrent.futures import Executor, ThreadPoolExecutor
from functools import partial
from typing import Any, Callable, Optional, TypeVar

ExecutionPolicy = str
ASYNC_EXECUTION = "async"
THREAD_EXECUTION = "thread"

_ReturnType = TypeVar("_ReturnType")


def validate_execution_policy(policy: ExecutionPolicy) -> None:
    """Validate the execution policy name used by the framework."""

    if policy not in {ASYNC_EXECUTION, THREAD_EXECUTION}:
        raise ValueError("Execution policy must be 'async' or 'thread'.")


class TasgiRuntime:
    """Run sync callables in worker threads while the event loop owns transport I/O."""

    def __init__(
        self,
        thread_pool_workers: Optional[int] = None,
        cpu_thread_pool_workers: Optional[int] = None,
    ):
        """Create worker executors for threaded handler execution."""

        self._thread_pool_workers = thread_pool_workers
        self._cpu_thread_pool_workers = cpu_thread_pool_workers
        self._executor: Optional[Executor] = None
        self._cpu_executor: Optional[Executor] = None
        self._started = False
        self._closed = False

    @property
    def started(self) -> bool:
        """Return whether the runtime executors are active."""

        return self._started

    @property
    def closed(self) -> bool:
        """Return whether the runtime has been shut down."""

        return self._closed

    async def startup(self) -> None:
        """Create worker executors if they are not already running."""

        if self._started:
            return
        self._executor = ThreadPoolExecutor(
            max_workers=self._thread_pool_workers,
            thread_name_prefix="tasgi-worker",
        )
        if self._cpu_thread_pool_workers is not None:
            self._cpu_executor = ThreadPoolExecutor(
                max_workers=self._cpu_thread_pool_workers,
                thread_name_prefix="tasgi-cpu-worker",
            )
        self._started = True
        self._closed = False

    async def run_sync(
        self,
        func: Callable[..., _ReturnType],
        *args: Any,
        use_cpu_pool: bool = False,
        **kwargs: Any,
    ) -> _ReturnType:
        """Execute a sync callable in the configured worker pool."""

        if not self._started:
            await self.startup()
        loop = asyncio.get_running_loop()
        call = partial(func, *args, **kwargs)
        executor = self._select_executor(use_cpu_pool=use_cpu_pool)
        return await loop.run_in_executor(executor, call)

    async def shutdown(self) -> None:
        """Shut down the worker pools owned by the runtime."""

        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=False)
            self._executor = None
        if self._cpu_executor is not None:
            self._cpu_executor.shutdown(wait=True, cancel_futures=False)
            self._cpu_executor = None
        self._started = False
        self._closed = True

    async def close(self) -> None:
        """Alias for shutdown."""

        await self.shutdown()

    def _select_executor(self, *, use_cpu_pool: bool) -> Executor:
        if use_cpu_pool and self._cpu_executor is not None:
            return self._cpu_executor
        if self._executor is None:
            raise RuntimeError("tasgi runtime has not been started.")
        return self._executor
