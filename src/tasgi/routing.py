"""Routing primitives for tasgi."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
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
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RouteMatch:
    """Result of router resolution."""

    route: Optional[Route]
    allowed_methods: list[str]
    route_params: dict[str, str] = field(default_factory=dict)


@dataclass
class _ParamRouteGroup:
    """Method table for one parameterized path pattern."""

    path: str
    segments: tuple[str, ...]
    param_names: tuple[Optional[str], ...]
    methods: dict[str, Route] = field(default_factory=dict)

    def match(self, path: str) -> Optional[dict[str, str]]:
        """Return extracted path parameters when the pattern matches."""

        candidate_segments = _split_path(path)
        if len(candidate_segments) != len(self.segments):
            return None

        params: dict[str, str] = {}
        for route_segment, candidate_segment, param_name in zip(
            self.segments,
            candidate_segments,
            self.param_names,
        ):
            if param_name is not None:
                params[param_name] = candidate_segment
                continue
            if route_segment != candidate_segment:
                return None
        return params


class Router:
    """Exact-first router with path params and 404/405 support."""

    def __init__(self) -> None:
        self._static_routes: dict[str, dict[str, Route]] = {}
        self._param_routes: dict[int, list[_ParamRouteGroup]] = {}

    def add_route(
        self,
        path: str,
        methods: list[str],
        handler: Handler,
        execution: Optional[ExecutionPolicy] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Register one handler for one or more HTTP methods."""

        _validate_route_path(path)
        if not methods:
            raise ValueError("Route registration requires at least one HTTP method.")

        route_metadata = dict(metadata or {})
        is_async = inspect.iscoroutinefunction(handler)
        route_segments = _split_path(path)
        param_names = _extract_param_names(route_segments, path)

        if any(name is not None for name in param_names):
            method_map = self._get_or_create_param_group(path, route_segments, param_names).methods
        else:
            method_map = self._static_routes.setdefault(path, {})

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
                metadata=dict(route_metadata),
            )

    def resolve(self, method: str, path: str) -> RouteMatch:
        """Resolve a request method/path pair into a route or 404/405 result."""

        normalized_method = method.upper()

        static_method_map = self._static_routes.get(path)
        if static_method_map is not None:
            route = static_method_map.get(normalized_method)
            if route is not None:
                return RouteMatch(route=route, allowed_methods=[normalized_method])
            return RouteMatch(route=None, allowed_methods=sorted(static_method_map))

        param_groups = self._param_routes.get(len(_split_path(path)), [])
        for group in param_groups:
            route_params = group.match(path)
            if route_params is None:
                continue

            route = group.methods.get(normalized_method)
            if route is not None:
                return RouteMatch(
                    route=route,
                    allowed_methods=[normalized_method],
                    route_params=route_params,
                )
            return RouteMatch(route=None, allowed_methods=sorted(group.methods))

        return RouteMatch(route=None, allowed_methods=[])

    def _get_or_create_param_group(
        self,
        path: str,
        segments: tuple[str, ...],
        param_names: tuple[Optional[str], ...],
    ) -> _ParamRouteGroup:
        groups = self._param_routes.setdefault(len(segments), [])
        for group in groups:
            if group.segments == segments and group.param_names == param_names:
                return group
            if _same_route_shape(group.segments, segments):
                raise ValueError(
                    "Ambiguous parameter route %s conflicts with %s." % (path, group.path)
                )

        group = _ParamRouteGroup(path=path, segments=segments, param_names=param_names)
        groups.append(group)
        return group


def _validate_route_path(path: str) -> None:
    if not path.startswith("/"):
        raise ValueError("Routes must use an absolute path.")


def _split_path(path: str) -> tuple[str, ...]:
    if path == "/":
        return ()
    return tuple(path[1:].split("/"))


def _extract_param_names(
    segments: tuple[str, ...],
    path: str,
) -> tuple[Optional[str], ...]:
    seen_names: set[str] = set()
    param_names: list[Optional[str]] = []

    for segment in segments:
        if not (segment.startswith("{") or segment.endswith("}")):
            param_names.append(None)
            continue

        if not (segment.startswith("{") and segment.endswith("}")):
            raise ValueError("Invalid path parameter segment in %s." % path)

        param_name = segment[1:-1].strip()
        if not param_name:
            raise ValueError("Path parameters must be non-empty in %s." % path)
        if "{" in param_name or "}" in param_name:
            raise ValueError("Invalid path parameter segment in %s." % path)
        if param_name in seen_names:
            raise ValueError("Duplicate path parameter %r in %s." % (param_name, path))

        seen_names.add(param_name)
        param_names.append(param_name)

    return tuple(param_names)


def _same_route_shape(
    left_segments: tuple[str, ...],
    right_segments: tuple[str, ...],
) -> bool:
    if len(left_segments) != len(right_segments):
        return False

    for left, right in zip(left_segments, right_segments):
        left_is_param = left.startswith("{") and left.endswith("}")
        right_is_param = right.startswith("{") and right.endswith("}")
        if left_is_param != right_is_param:
            return False
        if not left_is_param and left != right:
            return False
    return True
