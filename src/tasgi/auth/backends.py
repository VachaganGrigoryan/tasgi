"""Simple stdlib-only auth backends for tasgi."""

from __future__ import annotations

from typing import Any, Callable, Optional, Union

from .base import AuthBackend
from .models import AuthContext, Identity

ValidatorResult = Union[Identity, dict[str, Any], str, None]
Validator = Callable[[str], ValidatorResult]


class BearerTokenBackend(AuthBackend):
    """Authenticate ``Authorization: Bearer <token>`` credentials."""

    name = "bearer"

    def __init__(self, validator: Validator) -> None:
        self._validator = validator

    def authenticate(self, request) -> Optional[AuthContext]:
        header = request.header("authorization")
        if header is None:
            return None
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return None
        identity = _coerce_identity(self._validator(token))
        if identity is None:
            return None
        return AuthContext(
            identity=identity,
            scheme="bearer",
            credentials=token,
            backend=self.name,
        )


class APIKeyBackend(AuthBackend):
    """Authenticate API keys from one request header."""

    name = "api-key"

    def __init__(
        self,
        validator: Validator,
        *,
        header_name: str = "x-api-key",
    ) -> None:
        self._validator = validator
        self._header_name = header_name

    def authenticate(self, request) -> Optional[AuthContext]:
        api_key = request.header(self._header_name)
        if not api_key:
            return None
        identity = _coerce_identity(self._validator(api_key))
        if identity is None:
            return None
        return AuthContext(
            identity=identity,
            scheme="api-key",
            credentials=api_key,
            backend=self.name,
        )


def _coerce_identity(value: ValidatorResult) -> Optional[Identity]:
    if value is None:
        return None
    if isinstance(value, Identity):
        return value
    if isinstance(value, str):
        return Identity(subject=value)
    if isinstance(value, dict):
        data = dict(value)
        data["roles"] = frozenset(data.get("roles", ()))
        data["scopes"] = frozenset(data.get("scopes", ()))
        return Identity(**data)
    raise TypeError("Auth validator results must be Identity, dict, str, or None.")
