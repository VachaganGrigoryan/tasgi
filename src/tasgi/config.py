"""Application and runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .runtime import ASYNC_EXECUTION, ExecutionPolicy, validate_execution_policy


@dataclass(frozen=True)
class TasgiConfig:
    """Configuration for a tasgi application and runtime."""

    host: str = "127.0.0.1"
    port: int = 8000
    title: str = "tasgi"
    version: str = "0.1.0"
    description: Optional[str] = None
    debug: bool = False
    docs: bool = False
    openapi_url: Optional[str] = None
    docs_url: Optional[str] = None
    default_execution: ExecutionPolicy = ASYNC_EXECUTION
    thread_pool_workers: Optional[int] = None
    cpu_thread_pool_workers: Optional[int] = None
    max_request_body_size: int = 1_048_576
    request_timeout: Optional[float] = None
    graceful_shutdown_timeout: float = 5.0
    http2: bool = True
    tls_certfile: Optional[str] = None
    tls_keyfile: Optional[str] = None

    def __post_init__(self) -> None:
        """Validate config values eagerly so API errors fail fast."""

        validate_execution_policy(self.default_execution)
        _validate_positive_optional("thread_pool_workers", self.thread_pool_workers)
        _validate_positive_optional("cpu_thread_pool_workers", self.cpu_thread_pool_workers)
        if self.max_request_body_size <= 0:
            raise ValueError("max_request_body_size must be positive.")
        _validate_positive_optional("request_timeout", self.request_timeout)
        if self.graceful_shutdown_timeout <= 0:
            raise ValueError("graceful_shutdown_timeout must be positive.")
        if (self.tls_certfile is None) != (self.tls_keyfile is None):
            raise ValueError("tls_certfile and tls_keyfile must be provided together.")
        _validate_optional_route_path("openapi_url", self.openapi_url)
        _validate_optional_route_path("docs_url", self.docs_url)


def _validate_positive_optional(name: str, value) -> None:
    if value is None:
        return
    if value <= 0:
        raise ValueError("%s must be positive." % name)


def _validate_optional_route_path(name: str, value: Optional[str]) -> None:
    if value is None:
        return
    if not value.startswith("/"):
        raise ValueError("%s must start with '/'." % name)
