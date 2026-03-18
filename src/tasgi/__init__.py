"""Public tasgi API."""

from .auth import (
    APIKeyBackend,
    AuthBackend,
    AuthContext,
    AuthPolicy,
    BearerTokenBackend,
    BasicAuthBackend,
    Identity,
    RequireAuthenticated,
    RequireRole,
    RequireScope,
)
from .app import App, TasgiApp
from .asgi_server import ASGIServer
from .config import TasgiConfig
from .dependencies import APP_SCOPE, REQUEST_SCOPE, Depends
from .docs import OpenAPIDocs
from .main import run, serve
from .middleware import ExceptionMiddleware, LoggingMiddleware, TimingMiddleware
from .request import Request
from .response import JsonResponse, Response, StreamingResponse, TextResponse
from .routing import Router
from .runtime import ASYNC_EXECUTION, THREAD_EXECUTION, TasgiRuntime
from .state import AppState
from .websocket import WebSocket

__all__ = [
    "APIKeyBackend",
    "ASGIServer",
    "ASYNC_EXECUTION",
    "APP_SCOPE",
    "AppState",
    "App",
    "AuthBackend",
    "AuthContext",
    "AuthPolicy",
    "BearerTokenBackend",
    "BasicAuthBackend",
    "Depends",
    "ExceptionMiddleware",
    "Identity",
    "LoggingMiddleware",
    "OpenAPIDocs",
    "Request",
    "Response",
    "Router",
    "REQUEST_SCOPE",
    "RequireAuthenticated",
    "RequireRole",
    "RequireScope",
    "StreamingResponse",
    "TasgiApp",
    "TasgiConfig",
    "TasgiRuntime",
    "TextResponse",
    "TimingMiddleware",
    "WebSocket",
    "JsonResponse",
    "THREAD_EXECUTION",
    "run",
    "serve",
]

JSONResponse = JsonResponse
