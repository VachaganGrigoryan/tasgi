"""Explicit dependency resolution for tasgi handlers."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from .app import TasgiApp
    from .request import Request
    from .runtime import TasgiRuntime

REQUEST_SCOPE = "request"
APP_SCOPE = "app"


@dataclass(frozen=True)
class Depends:
    """Declare an explicit dependency for a handler parameter."""

    provider: Callable[..., Any]
    scope: str = REQUEST_SCOPE
    use_cache: bool = True

    def __post_init__(self) -> None:
        if self.scope not in {REQUEST_SCOPE, APP_SCOPE}:
            raise ValueError("Dependency scope must be 'request' or 'app'.")


class DependencyResolver:
    """Resolve handler dependencies in a predictable async/thread-safe way."""

    def __init__(self, app: "TasgiApp", runtime: "TasgiRuntime") -> None:
        self._app = app
        self._runtime = runtime
        self._app_cache: dict[Callable[..., Any], Any] = {}
        self._app_locks: dict[Callable[..., Any], Any] = {}

    async def resolve_handler(self, handler: Callable[..., Any], request: "Request") -> dict[str, Any]:
        """Resolve one handler signature into concrete keyword arguments."""

        return await self._resolve_callable(
            handler,
            request,
            request_cache={},
            current_scope=REQUEST_SCOPE,
        )

    def clear_app_cache(self) -> None:
        """Drop app-scoped dependency instances."""

        self._app_cache.clear()
        self._app_locks.clear()

    async def _resolve_callable(
        self,
        func: Callable[..., Any],
        request: "Request",
        *,
        request_cache: dict[Callable[..., Any], Any],
        current_scope: str,
    ) -> dict[str, Any]:
        try:
            signature = inspect.signature(func)
        except (TypeError, ValueError) as exc:
            raise TypeError("Unable to inspect dependency callable %r." % func) from exc

        kwargs: dict[str, Any] = {}
        for parameter in signature.parameters.values():
            if parameter.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
                raise TypeError("tasgi dependencies do not support *args or **kwargs parameters.")
            kwargs[parameter.name] = await self._resolve_parameter(
                parameter,
                request,
                request_cache=request_cache,
                current_scope=current_scope,
            )
        return kwargs

    async def _resolve_parameter(
        self,
        parameter: inspect.Parameter,
        request: "Request",
        *,
        request_cache: dict[Callable[..., Any], Any],
        current_scope: str,
    ) -> Any:
        if _is_request_parameter(parameter):
            if current_scope == APP_SCOPE:
                raise TypeError("App-scoped dependencies cannot depend on the request object.")
            return request
        if _is_app_parameter(parameter):
            return request.app

        default = parameter.default
        if isinstance(default, Depends):
            return await self._resolve_dependency(
                default,
                request,
                request_cache=request_cache,
                current_scope=current_scope,
            )

        if default is not inspect.Signature.empty:
            return default

        raise TypeError(
            "Unable to resolve parameter %r for %r. Use request/app injection, a default, or Depends(...)."
            % (parameter.name, parameter.annotation if parameter.annotation is not inspect.Signature.empty else parameter.name)
        )

    async def _resolve_dependency(
        self,
        dependency: Depends,
        request: "Request",
        *,
        request_cache: dict[Callable[..., Any], Any],
        current_scope: str,
    ) -> Any:
        if current_scope == APP_SCOPE and dependency.scope != APP_SCOPE:
            raise TypeError("App-scoped dependencies cannot depend on request-scoped dependencies.")

        if dependency.scope == APP_SCOPE:
            return await self._resolve_app_dependency(
                dependency,
                request,
                request_cache=request_cache,
            )
        return await self._resolve_request_dependency(
            dependency,
            request,
            request_cache=request_cache,
        )

    async def _resolve_request_dependency(
        self,
        dependency: Depends,
        request: "Request",
        *,
        request_cache: dict[Callable[..., Any], Any],
    ) -> Any:
        if dependency.use_cache and dependency.provider in request_cache:
            return request_cache[dependency.provider]

        value = await self._call_dependency(
            dependency.provider,
            request,
            request_cache=request_cache,
            current_scope=REQUEST_SCOPE,
        )
        if dependency.use_cache:
            request_cache[dependency.provider] = value
        return value

    async def _resolve_app_dependency(
        self,
        dependency: Depends,
        request: "Request",
        *,
        request_cache: dict[Callable[..., Any], Any],
    ) -> Any:
        if dependency.use_cache and dependency.provider in self._app_cache:
            return self._app_cache[dependency.provider]

        lock = self._app_locks.get(dependency.provider)
        if lock is None:
            import asyncio

            lock = asyncio.Lock()
            self._app_locks[dependency.provider] = lock

        async with lock:
            if dependency.use_cache and dependency.provider in self._app_cache:
                return self._app_cache[dependency.provider]

            value = await self._call_dependency(
                dependency.provider,
                request,
                request_cache=request_cache,
                current_scope=APP_SCOPE,
            )
            if dependency.use_cache:
                self._app_cache[dependency.provider] = value
            return value

    async def _call_dependency(
        self,
        func: Callable[..., Any],
        request: "Request",
        *,
        request_cache: dict[Callable[..., Any], Any],
        current_scope: str,
    ) -> Any:
        kwargs = await self._resolve_callable(
            func,
            request,
            request_cache=request_cache,
            current_scope=current_scope,
        )
        if _is_async_callable(func):
            return await func(**kwargs)
        return await self._runtime.run_sync(func, **kwargs)


def _is_async_callable(func: Callable[..., Any]) -> bool:
    if inspect.iscoroutinefunction(func):
        return True
    call = getattr(func, "__call__", None)
    return inspect.iscoroutinefunction(call)


def _is_request_parameter(parameter: inspect.Parameter) -> bool:
    annotation = parameter.annotation
    return parameter.name == "request" or getattr(annotation, "__name__", None) == "Request"


def _is_app_parameter(parameter: inspect.Parameter) -> bool:
    annotation = parameter.annotation
    return parameter.name == "app" or getattr(annotation, "__name__", None) == "TasgiApp"
