"""Main public application object for tasgi."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import inspect
from typing import Any, Optional

from .asgi import build_request, receive_request_body, send_response, validate_http_scope
from .config import TasgiConfig
from .dependencies import DependencyResolver
from .docs import OpenAPIDocs
from .exceptions import HTTPError, MethodNotAllowed
from .lifecycle import LifecycleManager
from .middleware import Middleware, NextHandler, is_async_middleware
from .response import JsonResponse, Response, StreamingResponse, TextResponse
from .routing import Handler, Route, Router
from .runtime import ASYNC_EXECUTION, THREAD_EXECUTION, ExecutionPolicy, TasgiRuntime, validate_execution_policy
from .schema import get_callable_type_hints, serialize_model_value
from .state import AppState
from .websocket import WebSocket


class TasgiApp:
    """Framework application object with routing, lifecycle, and execution control."""

    def __init__(
        self,
        config: Optional[TasgiConfig] = None,
        *,
        runtime: Optional[TasgiRuntime] = None,
        **config_overrides,
    ):
        """Create a tasgi application with config, state, router, and runtime."""

        if config is not None and config_overrides:
            raise ValueError("Pass either TasgiConfig or keyword config overrides, not both.")
        self.config = config or TasgiConfig(**config_overrides)
        self.state = AppState()
        self.router = Router()
        self.lifecycle = LifecycleManager()
        self._runtime = runtime or TasgiRuntime(
            thread_pool_workers=self.config.thread_pool_workers,
            cpu_thread_pool_workers=self.config.cpu_thread_pool_workers,
        )
        self._owns_runtime = runtime is None
        self._dependencies = DependencyResolver(self, self._runtime)
        self._startup_lock: Optional[asyncio.Lock] = None
        self._shutdown_lock: Optional[asyncio.Lock] = None
        self._middleware: list[Middleware] = []
        self._docs: Optional[OpenAPIDocs] = None
        self._lifecycle_state = "created"
        self._started = False
        self._closed = False
        self._configure_docs_from_config()
        self._register_builtin_docs_routes()

    @property
    def runtime(self) -> TasgiRuntime:
        """Expose the app runtime for inspection and advanced usage."""

        return self._runtime

    @property
    def lifecycle_state(self) -> str:
        """Return the current lifecycle state."""

        return self._lifecycle_state

    @property
    def docs(self) -> OpenAPIDocs:
        """Lazily create the optional OpenAPI docs registry."""

        if self._docs is None:
            self._docs = OpenAPIDocs()
        return self._docs

    @property
    def started(self) -> bool:
        """Return whether the app has completed startup."""

        return self._started

    def route(
        self,
        path: str,
        *,
        methods: Optional[list[str]] = None,
        execution: Optional[ExecutionPolicy] = None,
        metadata: Optional[dict[str, object]] = None,
        summary: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[list[str]] = None,
        operation_id: Optional[str] = None,
        request_model: Any = None,
        response_model: Any = None,
        request_schema: Optional[dict[str, object]] = None,
        response_schema: Optional[dict[str, object]] = None,
        status_code: Optional[int] = None,
        include_in_schema: bool = True,
    ):
        """Register a handler for one or more HTTP methods."""

        if execution is not None:
            validate_execution_policy(execution)
        resolved_methods = list(methods or ["GET"])
        route_metadata = _build_route_metadata(
            metadata=metadata,
            summary=summary,
            description=description,
            tags=tags,
            operation_id=operation_id,
            request_model=request_model,
            response_model=response_model,
            request_schema=request_schema,
            response_schema=response_schema,
            status_code=status_code,
            include_in_schema=include_in_schema,
        )

        def decorator(handler: Handler):
            is_async = inspect.iscoroutinefunction(handler)
            self._validate_handler_policy(path, is_async, execution)
            self.router.add_route(
                path,
                resolved_methods,
                handler,
                execution=execution,
                metadata=route_metadata,
            )
            return handler

        return decorator

    def get(
        self,
        path: str,
        *,
        execution: Optional[ExecutionPolicy] = None,
        metadata: Optional[dict[str, object]] = None,
        summary: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[list[str]] = None,
        operation_id: Optional[str] = None,
        request_model: Any = None,
        response_model: Any = None,
        request_schema: Optional[dict[str, object]] = None,
        response_schema: Optional[dict[str, object]] = None,
        status_code: Optional[int] = None,
        include_in_schema: bool = True,
    ):
        """Register a GET handler."""

        return self.route(
            path,
            methods=["GET"],
            execution=execution,
            metadata=metadata,
            summary=summary,
            description=description,
            tags=tags,
            operation_id=operation_id,
            request_model=request_model,
            response_model=response_model,
            request_schema=request_schema,
            response_schema=response_schema,
            status_code=status_code,
            include_in_schema=include_in_schema,
        )

    def post(
        self,
        path: str,
        *,
        execution: Optional[ExecutionPolicy] = None,
        metadata: Optional[dict[str, object]] = None,
        summary: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[list[str]] = None,
        operation_id: Optional[str] = None,
        request_model: Any = None,
        response_model: Any = None,
        request_schema: Optional[dict[str, object]] = None,
        response_schema: Optional[dict[str, object]] = None,
        status_code: Optional[int] = None,
        include_in_schema: bool = True,
    ):
        """Register a POST handler."""

        return self.route(
            path,
            methods=["POST"],
            execution=execution,
            metadata=metadata,
            summary=summary,
            description=description,
            tags=tags,
            operation_id=operation_id,
            request_model=request_model,
            response_model=response_model,
            request_schema=request_schema,
            response_schema=response_schema,
            status_code=status_code,
            include_in_schema=include_in_schema,
        )

    def put(
        self,
        path: str,
        *,
        execution: Optional[ExecutionPolicy] = None,
        metadata: Optional[dict[str, object]] = None,
        summary: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[list[str]] = None,
        operation_id: Optional[str] = None,
        request_model: Any = None,
        response_model: Any = None,
        request_schema: Optional[dict[str, object]] = None,
        response_schema: Optional[dict[str, object]] = None,
        status_code: Optional[int] = None,
        include_in_schema: bool = True,
    ):
        return self.route(
            path,
            methods=["PUT"],
            execution=execution,
            metadata=metadata,
            summary=summary,
            description=description,
            tags=tags,
            operation_id=operation_id,
            request_model=request_model,
            response_model=response_model,
            request_schema=request_schema,
            response_schema=response_schema,
            status_code=status_code,
            include_in_schema=include_in_schema,
        )

    def delete(
        self,
        path: str,
        *,
        execution: Optional[ExecutionPolicy] = None,
        metadata: Optional[dict[str, object]] = None,
        summary: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[list[str]] = None,
        operation_id: Optional[str] = None,
        request_model: Any = None,
        response_model: Any = None,
        request_schema: Optional[dict[str, object]] = None,
        response_schema: Optional[dict[str, object]] = None,
        status_code: Optional[int] = None,
        include_in_schema: bool = True,
    ):
        return self.route(
            path,
            methods=["DELETE"],
            execution=execution,
            metadata=metadata,
            summary=summary,
            description=description,
            tags=tags,
            operation_id=operation_id,
            request_model=request_model,
            response_model=response_model,
            request_schema=request_schema,
            response_schema=response_schema,
            status_code=status_code,
            include_in_schema=include_in_schema,
        )

    def websocket(
        self,
        path: str,
        *,
        metadata: Optional[dict[str, object]] = None,
    ):
        """Register an async WebSocket handler."""

        def decorator(handler: Handler):
            if not inspect.iscoroutinefunction(handler):
                raise ValueError("tasgi WebSocket handlers must be async.")
            self.router.add_websocket(path, handler, metadata=metadata)
            return handler

        return decorator

    def include_router(self, router: Router, *, prefix: str = "") -> None:
        """Register routes from another router, optionally under a shared prefix."""

        normalized_prefix = _normalize_router_prefix(prefix)
        for route in router.iter_routes():
            full_path = _join_router_prefix(normalized_prefix, route.path)
            if route.scope_type == "websocket":
                if not route.is_async:
                    raise ValueError("tasgi WebSocket handlers must be async.")
                self.router.add_websocket(
                    full_path,
                    route.handler,
                    metadata=dict(route.metadata),
                )
                continue

            self._validate_handler_policy(full_path, route.is_async, route.execution)
            self.router.add_route(
                full_path,
                [route.method],
                route.handler,
                execution=route.execution,
                metadata=dict(route.metadata),
            )

    def on_startup(self, func):
        """Register a startup hook."""

        return self.lifecycle.on_startup(func)

    def on_shutdown(self, func):
        """Register a shutdown hook."""

        return self.lifecycle.on_shutdown(func)

    def add_service(self, name: str, service: object) -> object:
        """Register a shared service on application state."""

        return self.state.set_service(name, service)

    def get_service(self, name: str, default: object = None) -> object:
        """Return a shared service or a default when it is absent."""

        return self.state.get_service(name, default)

    def require_service(self, name: str) -> object:
        """Return a shared service or raise when it is absent."""

        return self.state.require_service(name)

    def remove_service(self, name: str) -> object:
        """Remove and return a registered shared service."""

        return self.state.remove_service(name)

    def add_middleware(self, middleware: Middleware) -> Middleware:
        """Register request/response middleware."""

        if not is_async_middleware(middleware):
            raise ValueError("tasgi middleware must be async.")
        self._middleware.append(middleware)
        return middleware

    def configure_docs(
        self,
        *,
        title: Optional[str] = None,
        version: Optional[str] = None,
        description: Optional[str] = None,
    ) -> OpenAPIDocs:
        """Configure the optional OpenAPI document metadata."""

        docs = self.docs
        if title is not None:
            docs.title = title
        if version is not None:
            docs.version = version
        if description is not None:
            docs.description = description
        return docs

    def register_request_schema(
        self,
        path: str,
        method: str,
        schema: dict[str, object],
        *,
        media_type: str = "application/json",
        required: bool = True,
        description: Optional[str] = None,
    ) -> None:
        """Register a request-body schema for OpenAPI generation."""

        self.docs.register_request_schema(
            path,
            method,
            schema,
            media_type=media_type,
            required=required,
            description=description,
        )

    def register_response_schema(
        self,
        path: str,
        method: str,
        status_code: int,
        schema: dict[str, object],
        *,
        media_type: str = "application/json",
        description: Optional[str] = None,
    ) -> None:
        """Register one response schema for OpenAPI generation."""

        self.docs.register_response_schema(
            path,
            method,
            status_code,
            schema,
            media_type=media_type,
            description=description,
        )

    def openapi_schema(self) -> dict[str, object]:
        """Generate an OpenAPI document from the registered HTTP routes."""

        return self.docs.generate(self.router)

    def middleware(self, func: Middleware) -> Middleware:
        """Decorator form for registering middleware."""

        return self.add_middleware(func)

    async def startup(self) -> None:
        """Run startup hooks once."""

        async with self._get_startup_lock():
            if self._started:
                return
            self._lifecycle_state = "starting"
            try:
                await self._runtime.startup()
                await self.lifecycle.run_startup(self, self._runtime)
            except Exception:
                if self._owns_runtime:
                    await self._runtime.shutdown()
                self._lifecycle_state = "failed"
                self._started = False
                self._closed = True
                raise
            self._started = True
            self._closed = False
            self._lifecycle_state = "started"

    async def shutdown(self) -> None:
        """Run shutdown hooks and close owned runtime resources once."""

        async with self._get_shutdown_lock():
            if self._closed:
                return
            self._lifecycle_state = "stopping"
            try:
                if self._started:
                    await self.lifecycle.run_shutdown(self, self._runtime)
            finally:
                if self._owns_runtime:
                    await self._runtime.shutdown()
                self._dependencies.clear_app_cache()
                self._closed = True
                self._started = False
                self._lifecycle_state = "stopped"

    async def close(self) -> None:
        """Alias for shutdown used by the server entrypoint."""

        await self.shutdown()

    @asynccontextmanager
    async def lifespan(self):
        """Context manager form of the tasgi lifecycle."""

        await self.startup()
        try:
            yield self
        finally:
            await self.shutdown()

    async def __call__(self, scope, receive, send) -> None:
        """ASGI entrypoint used by the transport layer."""

        await self.startup()
        scope_type = scope.get("type")
        if scope_type == "websocket":
            await self._handle_websocket(scope, receive, send)
            return

        response: Response
        request = None
        try:
            validate_http_scope(scope)
            body = await receive_request_body(receive, self.config.max_request_body_size)
            route_match = self.router.resolve(str(scope["method"]), str(scope["path"]))

            if route_match.route is None:
                if route_match.allowed_methods:
                    raise MethodNotAllowed(route_match.allowed_methods)
                raise HTTPError(404, "Not Found")

            request = build_request(self, scope, body, route_params=route_match.route_params)
            response = await self._dispatch(route_match.route, request)
        except HTTPError as exc:
            response = self._http_error_response(exc)
        except Exception as exc:
            response = self._internal_error_response(exc, request=request)

        await send_response(send, response)

    async def _handle_websocket(self, scope, receive, send) -> None:
        route_match = self.router.resolve_websocket(str(scope["path"]))
        websocket = WebSocket.from_scope(
            self,
            scope,
            receive,
            send,
            route_params=route_match.route_params,
        )

        if route_match.route is None:
            await websocket.close(code=1008, reason="Not Found")
            return

        try:
            result = await route_match.route.handler(websocket)
            if result is not None:
                raise TypeError("tasgi WebSocket handlers must return None.")
        except Exception as exc:
            if not websocket.closed:
                if self.config.debug:
                    reason = "%s: %s" % (exc.__class__.__name__, exc)
                else:
                    reason = ""
                await websocket.close(code=1011, reason=reason)
            return

        if not websocket.closed:
            await websocket.close(code=1000)

    async def _dispatch(self, route: Route, request) -> Response:
        endpoint = self._build_middleware_chain(route)
        if self.config.request_timeout is not None:
            response = await asyncio.wait_for(endpoint(request), timeout=self.config.request_timeout)
        else:
            response = await endpoint(request)

        if not isinstance(response, Response):
            raise TypeError("tasgi handlers and middleware must return Response objects.")
        return response

    async def _dispatch_without_middleware(self, route: Route, request) -> Response:
        execution = self._resolve_execution(route)
        kwargs = await self._dependencies.resolve_handler(route.handler, request)
        if execution == ASYNC_EXECUTION:
            result = await route.handler(**kwargs)
        else:
            result = await self._runtime.run_sync(route.handler, **kwargs)
            if isinstance(result, StreamingResponse):
                result.bind_thread_runtime(self._runtime)

        return self._coerce_handler_result(route, result)

    def _build_middleware_chain(self, route: Route) -> NextHandler:
        async def endpoint(current_request) -> Response:
            return await self._dispatch_without_middleware(route, current_request)

        next_handler = endpoint
        for middleware in reversed(self._middleware):
            downstream = next_handler

            async def wrapped(
                current_request,
                current_middleware=middleware,
                current_downstream=downstream,
            ) -> Response:
                return await current_middleware(current_request, current_downstream)

            next_handler = wrapped
        return next_handler

    def _resolve_execution(self, route: Route) -> ExecutionPolicy:
        if route.execution is not None:
            return route.execution
        if route.is_async:
            return ASYNC_EXECUTION
        return THREAD_EXECUTION

    def _validate_handler_policy(
        self,
        path: str,
        is_async: bool,
        execution: Optional[ExecutionPolicy],
    ) -> None:
        if execution == ASYNC_EXECUTION and not is_async:
            raise ValueError(
                "Sync handlers cannot declare execution='async'; use 'thread' or make the handler async."
            )
        if execution == THREAD_EXECUTION and is_async:
            raise ValueError(
                "Async handlers cannot declare execution='thread'; use 'async' or make the handler sync."
            )
        del path

    def _http_error_response(self, exc: HTTPError) -> Response:
        return TextResponse(
            exc.detail,
            status_code=exc.status_code,
            headers=exc.headers,
        )

    def _internal_error_response(self, exc: Exception, *, request=None) -> Response:
        if self.config.debug:
            detail = "%s: %s" % (exc.__class__.__name__, exc)
            if request is not None:
                detail = "%s on %s %s" % (detail, request.method, request.path)
            body = "Internal Server Error: %s" % detail
        else:
            body = "Internal Server Error"
        return TextResponse(body, status_code=500)

    def _get_startup_lock(self) -> asyncio.Lock:
        if self._startup_lock is None:
            self._startup_lock = asyncio.Lock()
        return self._startup_lock

    def _get_shutdown_lock(self) -> asyncio.Lock:
        if self._shutdown_lock is None:
            self._shutdown_lock = asyncio.Lock()
        return self._shutdown_lock

    def _configure_docs_from_config(self) -> None:
        self.configure_docs(
            title=self.config.title,
            version=self.config.version,
            description=self.config.description,
        )

    def _register_builtin_docs_routes(self) -> None:
        openapi_url = self.config.openapi_url or ("/openapi.json" if self.config.docs else None)
        docs_url = self.config.docs_url or ("/docs" if self.config.docs else None)
        if docs_url and not openapi_url:
            openapi_url = "/openapi.json"
        if openapi_url is not None:
            self._register_openapi_route(openapi_url)
        if docs_url is not None and openapi_url is not None:
            self._register_docs_route(docs_url, openapi_url)

    def _register_openapi_route(self, path: str) -> None:
        @self.get(
            path,
            include_in_schema=False,
            summary="OpenAPI document",
            response_schema={"type": "object"},
        )
        async def openapi_route(request) -> JsonResponse:
            return JsonResponse(self.openapi_schema())

        del openapi_route

    def _register_docs_route(self, path: str, openapi_url: str) -> None:
        @self.get(
            path,
            include_in_schema=False,
            summary="Swagger UI",
            response_schema={"type": "string"},
            response_model=str,
        )
        async def docs_route(request) -> Response:
            return Response(
                self.docs.swagger_ui_html(openapi_url=openapi_url, title="%s docs" % self.docs.title),
                media_type="text/html; charset=utf-8",
            )

        del docs_route

    def _coerce_handler_result(self, route: Route, result: Any) -> Response:
        if isinstance(result, Response):
            return result

        status_code = int(route.metadata.get("status_code", 200))
        response_model = route.metadata.get("response_model") or _infer_handler_response_model(route.handler)
        if response_model is None:
            raise TypeError("tasgi handlers must return Response objects.")

        if result is None:
            return Response(b"", status_code=status_code)
        if isinstance(result, bytes):
            return Response(result, status_code=status_code, media_type="application/octet-stream")
        if isinstance(result, str):
            return TextResponse(result, status_code=status_code)
        return JsonResponse(serialize_model_value(result), status_code=status_code)


def _normalize_router_prefix(prefix: str) -> str:
    if not prefix:
        return ""
    if not prefix.startswith("/"):
        raise ValueError("Router prefix must be empty or start with '/'.")
    if prefix == "/":
        return ""
    return prefix.rstrip("/")


def _join_router_prefix(prefix: str, path: str) -> str:
    if not prefix:
        return path
    if path == "/":
        return prefix
    return prefix + path


def _build_route_metadata(
    *,
    metadata: Optional[dict[str, object]],
    summary: Optional[str],
    description: Optional[str],
    tags: Optional[list[str]],
    operation_id: Optional[str],
    request_model: Any,
    response_model: Any,
    request_schema: Optional[dict[str, object]],
    response_schema: Optional[dict[str, object]],
    status_code: Optional[int],
    include_in_schema: bool,
) -> dict[str, object]:
    route_metadata = dict(metadata or {})
    if summary is not None:
        route_metadata["summary"] = summary
    if description is not None:
        route_metadata["description"] = description
    if tags is not None:
        route_metadata["tags"] = list(tags)
    if operation_id is not None:
        route_metadata["operation_id"] = operation_id
    if request_model is not None:
        route_metadata["request_model"] = request_model
    if response_model is not None:
        route_metadata["response_model"] = response_model
    if request_schema is not None:
        route_metadata["request_schema"] = dict(request_schema)
    if response_schema is not None:
        route_metadata["response_schema"] = dict(response_schema)
    if status_code is not None:
        route_metadata["status_code"] = int(status_code)
    if include_in_schema is False:
        route_metadata["include_in_schema"] = False
    return route_metadata


def _infer_handler_response_model(handler: Handler) -> Any:
    annotation = get_callable_type_hints(handler).get("return", inspect.signature(handler).return_annotation)
    if annotation is inspect.Signature.empty:
        return None
    if inspect.isclass(annotation) and issubclass(annotation, Response):
        return None
    return annotation


App = TasgiApp
