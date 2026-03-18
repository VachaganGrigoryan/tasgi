"""Optional OpenAPI document generation for tasgi applications."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .routing import Route, Router

JSONSchema = dict[str, Any]


@dataclass(frozen=True)
class RequestSchema:
    """One registered request-body schema for an HTTP operation."""

    schema: JSONSchema
    media_type: str = "application/json"
    required: bool = True
    description: Optional[str] = None


@dataclass(frozen=True)
class ResponseSchema:
    """One registered response schema for an HTTP operation."""

    status_code: int
    schema: JSONSchema
    media_type: str = "application/json"
    description: Optional[str] = None


@dataclass
class OpenAPIDocs:
    """Collect route docs metadata and emit a minimal OpenAPI document."""

    title: str = "tasgi"
    version: str = "0.1.0"
    description: Optional[str] = None
    _request_schemas: dict[tuple[str, str], RequestSchema] = field(default_factory=dict)
    _response_schemas: dict[tuple[str, str], dict[int, ResponseSchema]] = field(default_factory=dict)

    def register_request_schema(
        self,
        path: str,
        method: str,
        schema: JSONSchema,
        *,
        media_type: str = "application/json",
        required: bool = True,
        description: Optional[str] = None,
    ) -> None:
        """Register a request schema for one HTTP operation."""

        self._request_schemas[(path, method.upper())] = RequestSchema(
            schema=dict(schema),
            media_type=media_type,
            required=required,
            description=description,
        )

    def register_response_schema(
        self,
        path: str,
        method: str,
        status_code: int,
        schema: JSONSchema,
        *,
        media_type: str = "application/json",
        description: Optional[str] = None,
    ) -> None:
        """Register one response schema for one HTTP operation."""

        operation_key = (path, method.upper())
        responses = self._response_schemas.setdefault(operation_key, {})
        responses[int(status_code)] = ResponseSchema(
            status_code=int(status_code),
            schema=dict(schema),
            media_type=media_type,
            description=description,
        )

    def generate(self, router: Router) -> dict[str, Any]:
        """Build a minimal OpenAPI document from registered routes and schemas."""

        paths: dict[str, dict[str, Any]] = {}
        for route in router.iter_routes(scope_type="http"):
            path_item = paths.setdefault(route.path, {})
            path_item[route.method.lower()] = self._build_operation(route)

        document: dict[str, Any] = {
            "openapi": "3.1.0",
            "info": {
                "title": self.title,
                "version": self.version,
            },
            "paths": paths,
        }
        if self.description:
            document["info"]["description"] = self.description
        return document

    def _build_operation(self, route: Route) -> dict[str, Any]:
        operation: dict[str, Any] = {
            "responses": self._build_responses(route),
        }
        if route.metadata.get("summary") is not None:
            operation["summary"] = route.metadata["summary"]
        if route.metadata.get("description") is not None:
            operation["description"] = route.metadata["description"]
        if route.metadata.get("tags") is not None:
            operation["tags"] = list(route.metadata["tags"])
        if route.metadata.get("operation_id") is not None:
            operation["operationId"] = route.metadata["operation_id"]
        if route.metadata.get("deprecated") is not None:
            operation["deprecated"] = bool(route.metadata["deprecated"])

        parameters = _build_path_parameters(route.path)
        if parameters:
            operation["parameters"] = parameters

        request_schema = self._request_schemas.get((route.path, route.method))
        if request_schema is not None:
            operation["requestBody"] = {
                "required": request_schema.required,
                "content": {
                    request_schema.media_type: {
                        "schema": dict(request_schema.schema),
                    }
                },
            }
            if request_schema.description is not None:
                operation["requestBody"]["description"] = request_schema.description

        operation["x-tasgi-execution"] = route.execution or ("async" if route.is_async else "thread")
        return operation

    def _build_responses(self, route: Route) -> dict[str, Any]:
        registered = self._response_schemas.get((route.path, route.method), {})
        if not registered:
            return {"200": {"description": "Successful Response"}}

        responses: dict[str, Any] = {}
        for status_code in sorted(registered):
            response = registered[status_code]
            responses[str(status_code)] = {
                "description": response.description or "HTTP %s response" % status_code,
                "content": {
                    response.media_type: {
                        "schema": dict(response.schema),
                    }
                },
            }
        return responses


def _build_path_parameters(path: str) -> list[dict[str, Any]]:
    parameters: list[dict[str, Any]] = []
    for segment in path.strip("/").split("/"):
        if not segment.startswith("{") or not segment.endswith("}"):
            continue
        name = segment[1:-1]
        parameters.append(
            {
                "name": name,
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
            }
        )
    return parameters
