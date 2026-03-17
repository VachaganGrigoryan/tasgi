"""Routing primitives for tasgi."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .runtime import ExecutionPolicy

Handler = Callable[[Any], Any]


@dataclass(frozen=True)
class Route:
    """Route metadata used by the application dispatcher."""

    path: str
    method: str
    handler: Handler
    is_async: bool
    execution: Optional[ExecutionPolicy]


@dataclass(frozen=True)
class RouteMatch:
    """Result of router resolution."""

    route: Optional[Route]
    allowed_methods: list[str]


class Router:
    """Exact-path router with method matching and 404/405 support."""

    def __init__(self) -> None:
        self._routes: dict[str, dict[str, Route]] = {}

    def add_route(
        self,
        path: str,
        methods: list[str],
        handler: Handler,
        execution: Optional[ExecutionPolicy] = None,
    ) -> None:
        """Register one handler for one or more HTTP methods."""

        if not path.startswith("/"):
            raise ValueError("Routes must use an absolute path.")
        if not methods:
            raise ValueError("Route registration requires at least one HTTP method.")

        method_map = self._routes.setdefault(path, {})
        is_async = inspect.iscoroutinefunction(handler)
        for method in methods:
            normalized_method = method.upper()
            if normalized_method in method_map:
                raise ValueError(
                    "Route already registered for %s %s." % (normalized_method, path)
                )
            method_map[normalized_method] = Route(
                path=path,
                method=normalized_method,
                handler=handler,
                is_async=is_async,
                execution=execution,
            )

    def resolve(self, method: str, path: str) -> RouteMatch:
        """Resolve a request method/path pair into a route or 404/405 result."""

        method_map = self._routes.get(path)
        if method_map is None:
            return RouteMatch(route=None, allowed_methods=[])

        normalized_method = method.upper()
        route = method_map.get(normalized_method)
        if route is not None:
            return RouteMatch(route=route, allowed_methods=[normalized_method])

        return RouteMatch(route=None, allowed_methods=sorted(method_map))
