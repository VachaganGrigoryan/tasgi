"""Framework middleware support."""

from __future__ import annotations

import inspect
import time
from typing import Awaitable, Callable, Optional, Protocol

from .request import Request
from .response import Response

NextHandler = Callable[[Request], Awaitable[Response]]


class Middleware(Protocol):
    """Protocol for tasgi request/response middleware."""

    async def __call__(self, request: Request, call_next: NextHandler) -> Response:
        """Process a request and delegate to the next middleware/handler."""


def is_async_middleware(middleware) -> bool:
    """Return whether a middleware object exposes an async call interface."""

    return inspect.iscoroutinefunction(middleware) or inspect.iscoroutinefunction(
        getattr(middleware, "__call__", None)
    )


class LoggingMiddleware:
    """Minimal request logging middleware."""

    def __init__(self, logger: Optional[Callable[[str], None]] = None):
        self._logger = logger or print

    async def __call__(self, request: Request, call_next: NextHandler) -> Response:
        self._logger("tasgi request started: %s %s" % (request.method, request.path))
        try:
            response = await call_next(request)
        except Exception as exc:
            self._logger(
                "tasgi request failed: %s %s -> %s"
                % (request.method, request.path, exc.__class__.__name__)
            )
            raise
        self._logger(
            "tasgi request completed: %s %s -> %s"
            % (request.method, request.path, response.status_code)
        )
        return response


class TimingMiddleware:
    """Add a process-time header to each response."""

    def __init__(self, header_name: str = "x-process-time"):
        self._header_name = header_name.encode("latin-1")

    async def __call__(self, request: Request, call_next: NextHandler) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - start
        response.headers.append((self._header_name, ("%.6f" % elapsed).encode("latin-1")))
        return response


class ExceptionMiddleware:
    """Catch unhandled exceptions and convert them into framework error responses."""

    async def __call__(self, request: Request, call_next: NextHandler) -> Response:
        try:
            return await call_next(request)
        except Exception as exc:
            return request.app._internal_error_response(exc, request=request)
