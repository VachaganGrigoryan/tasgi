"""Simple stdlib-only auth backends for tasgi."""

from __future__ import annotations

import base64
import binascii
from typing import Any, Callable, Optional, Union

from .base import AuthBackend
from .models import AuthContext, Identity

ValidatorResult = Union[Identity, dict[str, Any], str, None]
Validator = Callable[[str], ValidatorResult]
BasicValidator = Callable[[str, str], ValidatorResult]


class BearerTokenBackend(AuthBackend):
    """Authenticate ``Authorization: Bearer <token>`` credentials."""

    name = "bearer"

    def __init__(
        self,
        validator: Validator,
        *,
        security_scheme_name: str = "bearerAuth",
        bearer_format: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        self._validator = validator
        self._security_scheme_name = security_scheme_name
        self._bearer_format = bearer_format
        self._description = description

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

    def openapi_security_scheme_name(self) -> str:
        return self._security_scheme_name

    def openapi_security_scheme(self) -> dict[str, Any]:
        scheme: dict[str, Any] = {
            "type": "http",
            "scheme": "bearer",
        }
        if self._bearer_format is not None:
            scheme["bearerFormat"] = self._bearer_format
        if self._description is not None:
            scheme["description"] = self._description
        return scheme


class APIKeyBackend(AuthBackend):
    """Authenticate API keys from one request header."""

    name = "api-key"

    def __init__(
        self,
        validator: Validator,
        *,
        header_name: str = "x-api-key",
        security_scheme_name: str = "apiKeyAuth",
        in_: str = "header",
        description: Optional[str] = None,
    ) -> None:
        self._validator = validator
        self._header_name = header_name
        self._security_scheme_name = security_scheme_name
        self._location = in_
        self._description = description

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

    def openapi_security_scheme_name(self) -> str:
        return self._security_scheme_name

    def openapi_security_scheme(self) -> dict[str, Any]:
        scheme: dict[str, Any] = {
            "type": "apiKey",
            "name": self._header_name,
            "in": self._location,
        }
        if self._description is not None:
            scheme["description"] = self._description
        return scheme


class BasicAuthBackend(AuthBackend):
    """Authenticate ``Authorization: Basic ...`` credentials."""

    name = "basic"

    def __init__(
        self,
        validator: BasicValidator,
        *,
        security_scheme_name: str = "basicAuth",
        description: Optional[str] = None,
    ) -> None:
        self._validator = validator
        self._security_scheme_name = security_scheme_name
        self._description = description

    def authenticate(self, request) -> Optional[AuthContext]:
        header = request.header("authorization")
        if header is None:
            return None
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "basic" or not value:
            return None
        try:
            decoded = base64.b64decode(value.encode("ascii"), validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError, binascii.Error):
            return None
        username, separator, password = decoded.partition(":")
        if not separator:
            return None
        identity = _coerce_identity(self._validator(username, password))
        if identity is None:
            return None
        return AuthContext(
            identity=identity,
            scheme="basic",
            credentials=username,
            backend=self.name,
        )

    def openapi_security_scheme_name(self) -> str:
        return self._security_scheme_name

    def openapi_security_scheme(self) -> dict[str, Any]:
        scheme: dict[str, Any] = {
            "type": "http",
            "scheme": "basic",
        }
        if self._description is not None:
            scheme["description"] = self._description
        return scheme


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
