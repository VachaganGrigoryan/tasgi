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
    scope_type: str = "http"
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


@dataclass
class _WebSocketParamRoute:
    """One parameterized WebSocket route entry."""

    group: _ParamRouteGroup
    route: Route


class Router:
    """Exact-first router with path params and 404/405 support."""

    def __init__(self) -> None:
        self._http_static_routes: dict[str, dict[str, Route]] = {}
        self._http_param_routes: dict[int, list[_ParamRouteGroup]] = {}
        self._websocket_static_routes: dict[str, Route] = {}
        self._websocket_param_routes: dict[int, list[_WebSocketParamRoute]] = {}

    def add_route(
        self,
        path: str,
        methods: list[str],
        handler: Handler,
        execution: Optional[ExecutionPolicy] = None,
        metadata: Optional[dict[str, Any]] = None,
        *,
        scope_type: str = "http",
    ) -> None:
        """Register one handler for one or more HTTP methods."""

        _validate_route_path(path)
        if scope_type == "http" and not methods:
            raise ValueError("Route registration requires at least one HTTP method.")
        if scope_type not in {"http", "websocket"}:
            raise ValueError("Unsupported route scope type %r." % scope_type)

        route_metadata = dict(metadata or {})
        is_async = inspect.iscoroutinefunction(handler)
        route_segments = _split_path(path)
        param_names = _extract_param_names(route_segments, path)

        if scope_type == "websocket":
            route = Route(
                path=path,
                method="WEBSOCKET",
                handler=handler,
                is_async=is_async,
                execution=execution,
                scope_type="websocket",
                metadata=dict(route_metadata),
            )
            if any(name is not None for name in param_names):
                self._add_websocket_param_route(path, route_segments, param_names, route)
            else:
                if path in self._websocket_static_routes:
                    raise ValueError("Route already registered for WEBSOCKET %s." % path)
                self._websocket_static_routes[path] = route
            return

        if any(name is not None for name in param_names):
            method_map = self._get_or_create_param_group(
                self._http_param_routes,
                path,
                route_segments,
                param_names,
            ).methods
        else:
            method_map = self._http_static_routes.setdefault(path, {})

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
                scope_type="http",
                metadata=dict(route_metadata),
            )

    def route(
        self,
        path: str,
        *,
        methods: Optional[list[str]] = None,
        execution: Optional[ExecutionPolicy] = None,
        metadata: Optional[dict[str, Any]] = None,
    ):
        """Decorator form for registering one or more HTTP methods."""

        resolved_methods = list(methods or ["GET"])

        def decorator(handler: Handler):
            self.add_route(
                path,
                resolved_methods,
                handler,
                execution=execution,
                metadata=metadata,
            )
            return handler

        return decorator

    def get(
        self,
        path: str,
        *,
        execution: Optional[ExecutionPolicy] = None,
        metadata: Optional[dict[str, Any]] = None,
    ):
        """Register a GET handler on this router."""

        return self.route(path, methods=["GET"], execution=execution, metadata=metadata)

    def post(
        self,
        path: str,
        *,
        execution: Optional[ExecutionPolicy] = None,
        metadata: Optional[dict[str, Any]] = None,
    ):
        """Register a POST handler on this router."""

        return self.route(path, methods=["POST"], execution=execution, metadata=metadata)

    def put(
        self,
        path: str,
        *,
        execution: Optional[ExecutionPolicy] = None,
        metadata: Optional[dict[str, Any]] = None,
    ):
        """Register a PUT handler on this router."""

        return self.route(path, methods=["PUT"], execution=execution, metadata=metadata)

    def delete(
        self,
        path: str,
        *,
        execution: Optional[ExecutionPolicy] = None,
        metadata: Optional[dict[str, Any]] = None,
    ):
        """Register a DELETE handler on this router."""

        return self.route(path, methods=["DELETE"], execution=execution, metadata=metadata)

    def resolve(self, method: str, path: str) -> RouteMatch:
        """Resolve a request method/path pair into a route or 404/405 result."""

        normalized_method = method.upper()

        static_method_map = self._http_static_routes.get(path)
        if static_method_map is not None:
            route = static_method_map.get(normalized_method)
            if route is not None:
                return RouteMatch(route=route, allowed_methods=[normalized_method])
            return RouteMatch(route=None, allowed_methods=sorted(static_method_map))

        param_groups = self._http_param_routes.get(len(_split_path(path)), [])
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

    def add_websocket(
        self,
        path: str,
        handler: Handler,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Register one WebSocket handler."""

        self.add_route(
            path,
            [],
            handler,
            execution=None,
            metadata=metadata,
            scope_type="websocket",
        )

    def websocket(
        self,
        path: str,
        *,
        metadata: Optional[dict[str, Any]] = None,
    ):
        """Decorator form for registering one WebSocket handler."""

        def decorator(handler: Handler):
            self.add_websocket(path, handler, metadata=metadata)
            return handler

        return decorator

    def resolve_websocket(self, path: str) -> RouteMatch:
        """Resolve a WebSocket path into a route or a not-found result."""

        route = self._websocket_static_routes.get(path)
        if route is not None:
            return RouteMatch(route=route, allowed_methods=[])

        for param_route in self._websocket_param_routes.get(len(_split_path(path)), []):
            route_params = param_route.group.match(path)
            if route_params is not None:
                return RouteMatch(
                    route=param_route.route,
                    allowed_methods=[],
                    route_params=route_params,
                )

        return RouteMatch(route=None, allowed_methods=[])

    def iter_routes(self, *, scope_type: Optional[str] = None) -> list[Route]:
        """Return registered routes for optional inspection or documentation."""

        routes: list[Route] = []
        if scope_type in {None, "http"}:
            for method_map in self._http_static_routes.values():
                routes.extend(method_map.values())
            for groups in self._http_param_routes.values():
                for group in groups:
                    routes.extend(group.methods.values())
        if scope_type in {None, "websocket"}:
            routes.extend(self._websocket_static_routes.values())
            routes.extend(param_route.route for groups in self._websocket_param_routes.values() for param_route in groups)
        return sorted(routes, key=lambda route: (route.scope_type, route.path, route.method))

    def _add_websocket_param_route(
        self,
        path: str,
        segments: tuple[str, ...],
        param_names: tuple[Optional[str], ...],
        route: Route,
    ) -> None:
        routes = self._websocket_param_routes.setdefault(len(segments), [])
        for existing in routes:
            group = existing.group
            if group.segments == segments and group.param_names == param_names:
                raise ValueError("Route already registered for WEBSOCKET %s." % path)
            if _same_route_shape(group.segments, segments):
                raise ValueError(
                    "Ambiguous parameter route %s conflicts with %s." % (path, group.path)
                )

        routes.append(
            _WebSocketParamRoute(
                group=_ParamRouteGroup(path=path, segments=segments, param_names=param_names),
                route=route,
            )
        )

    def _get_or_create_param_group(
        self,
        store: dict[int, list[_ParamRouteGroup]],
        path: str,
        segments: tuple[str, ...],
        param_names: tuple[Optional[str], ...],
    ) -> _ParamRouteGroup:
        groups = store.setdefault(len(segments), [])
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
