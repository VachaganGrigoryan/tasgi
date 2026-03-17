"""Framework exceptions used by tasgi."""

from __future__ import annotations

from typing import Optional

from .types import Header


class TasgiError(Exception):
    """Base class for tasgi framework errors."""


class HTTPError(TasgiError):
    """HTTP error that should be converted into a response."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        *,
        headers: Optional[list[Header]] = None,
    ):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.headers = list(headers or [])


class MethodNotAllowed(HTTPError):
    """Raised when a path exists but the method is not allowed."""

    def __init__(self, allowed_methods: list[str]):
        allow_value = ", ".join(sorted(allowed_methods)).encode("latin-1")
        super().__init__(
            405,
            "Method Not Allowed",
            headers=[(b"allow", allow_value)],
        )
