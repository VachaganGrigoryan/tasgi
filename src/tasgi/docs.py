"""Optional OpenAPI generation and Swagger UI helpers for tasgi."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Optional

from .auth.base import AuthBackend, AuthPolicy
from .response import JsonResponse, Response, StreamingResponse, TextResponse
from .routing import Route, Router
from .schema import JSONSchema, get_callable_type_hints, infer_json_schema


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
        operation_key = (path, method.upper())
        responses = self._response_schemas.setdefault(operation_key, {})
        responses[int(status_code)] = ResponseSchema(
            status_code=int(status_code),
            schema=dict(schema),
            media_type=media_type,
            description=description,
        )

    def generate(
        self,
        router: Router,
        *,
        default_auth_backend: Optional[AuthBackend] = None,
    ) -> dict[str, Any]:
        paths: dict[str, dict[str, Any]] = {}
        security_schemes: dict[str, dict[str, Any]] = {}
        for route in router.iter_routes(scope_type="http"):
            if route.metadata.get("include_in_schema", True) is False:
                continue
            path_item = paths.setdefault(route.path, {})
            path_item[route.method.lower()] = self._build_operation(
                route,
                default_auth_backend=default_auth_backend,
                security_schemes=security_schemes,
            )

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
        if security_schemes:
            document["components"] = {
                "securitySchemes": security_schemes,
            }
        return document

    def swagger_ui_html(self, *, openapi_url: str, title: Optional[str] = None) -> str:
        ui_title = title or self.title
        return """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{title}</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
    <style>
      body {{ margin: 0; background: #faf7f1; }}
      .topbar {{ display: none; }}
    </style>
  </head>
  <body>
    <div id="swagger-ui"></div>
    <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
    <script>
      window.onload = function () {{
        window.ui = SwaggerUIBundle({{
          url: "{openapi_url}",
          dom_id: "#swagger-ui",
          deepLinking: true,
          displayRequestDuration: true,
          presets: [SwaggerUIBundle.presets.apis]
        }});
      }};
    </script>
  </body>
</html>
""".format(title=_escape_html(ui_title), openapi_url=_escape_html(openapi_url))

    def _build_operation(
        self,
        route: Route,
        *,
        default_auth_backend: Optional[AuthBackend],
        security_schemes: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        operation: dict[str, Any] = {
            "responses": self._build_responses(route),
        }
        summary = route.metadata.get("summary")
        description = route.metadata.get("description")
        tags = route.metadata.get("tags")
        operation_id = route.metadata.get("operation_id")
        deprecated = route.metadata.get("deprecated")
        if summary is not None:
            operation["summary"] = summary
        if description is not None:
            operation["description"] = description
        if tags is not None:
            operation["tags"] = list(tags)
        if operation_id is not None:
            operation["operationId"] = operation_id
        if deprecated is not None:
            operation["deprecated"] = bool(deprecated)

        parameters = _build_path_parameters(route.path)
        if parameters:
            operation["parameters"] = parameters

        request_schema = self._request_schema_for_route(route)
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

        security = self._security_for_route(
            route,
            default_auth_backend=default_auth_backend,
            security_schemes=security_schemes,
        )
        if security is not None:
            operation["security"] = security

        operation["x-tasgi-execution"] = route.execution or ("async" if route.is_async else "thread")
        return operation

    def _build_responses(self, route: Route) -> dict[str, Any]:
        responses: dict[str, Any] = {}

        for status_code, response_doc in sorted(dict(route.metadata.get("responses", {})).items()):
            responses[str(int(status_code))] = _serialize_response_doc(int(status_code), response_doc)

        registered = self._response_schemas.get((route.path, route.method))
        if registered:
            for status_code, response in sorted(registered.items()):
                responses[str(status_code)] = {
                    "description": response.description or "HTTP %s response" % status_code,
                    "content": {
                        response.media_type: {
                            "schema": dict(response.schema),
                        }
                    },
                }

        inferred = self._infer_response_schema(route)
        if inferred is not None:
            status_code, schema, media_type = inferred
            responses.setdefault(
                str(status_code),
                {
                    "description": "HTTP %s response" % status_code,
                    "content": {
                        media_type: {
                            "schema": schema,
                        }
                    },
                },
            )

        if responses:
            return responses

        return {"200": {"description": "Successful Response"}}

    def _request_schema_for_route(self, route: Route) -> Optional[RequestSchema]:
        registered = self._request_schemas.get((route.path, route.method))
        if registered is not None:
            return registered

        if route.metadata.get("request_schema") is not None:
            return RequestSchema(
                schema=dict(route.metadata["request_schema"]),
                media_type=route.metadata.get("request_media_type", "application/json"),
                required=route.metadata.get("request_required", True),
                description=route.metadata.get("request_description"),
            )

        model = route.metadata.get("request_model") or _infer_request_model(route.handler)
        if model is None:
            return None
        return RequestSchema(
            schema=infer_json_schema(model),
            media_type=route.metadata.get("request_media_type", "application/json"),
            required=route.metadata.get("request_required", True),
            description=route.metadata.get("request_description"),
        )

    def _infer_response_schema(self, route: Route) -> Optional[tuple[int, JSONSchema, str]]:
        if route.metadata.get("response_schema") is not None:
            return (
                int(route.metadata.get("status_code", 200)),
                dict(route.metadata["response_schema"]),
                route.metadata.get("response_media_type", "application/json"),
            )

        model = route.metadata.get("response_model") or _infer_response_model(route.handler)
        if model is None:
            return None

        media_type = route.metadata.get("response_media_type", "application/json")
        if model in {str, bytes}:
            media_type = "text/plain" if model is str else "application/octet-stream"
        return (
            int(route.metadata.get("status_code", 200)),
            infer_json_schema(model),
            media_type,
        )

    def _security_for_route(
        self,
        route: Route,
        *,
        default_auth_backend: Optional[AuthBackend],
        security_schemes: dict[str, dict[str, Any]],
    ) -> Optional[list[dict[str, list[Any]]]]:
        auth_setting = route.metadata.get("auth")
        route_backend = route.metadata.get("auth_backend")

        if auth_setting is False:
            return []

        backend: Optional[AuthBackend]
        if isinstance(auth_setting, AuthBackend):
            backend = auth_setting
        elif isinstance(route_backend, AuthBackend):
            backend = route_backend
        elif auth_setting is True or isinstance(auth_setting, AuthPolicy):
            backend = default_auth_backend
        elif auth_setting is None:
            if route_backend is None:
                return None
            backend = default_auth_backend if not isinstance(route_backend, AuthBackend) else route_backend
        else:
            return None

        if backend is None:
            return None

        scheme = backend.openapi_security_scheme()
        if scheme is None:
            return None

        scheme_name = backend.openapi_security_scheme_name()
        security_schemes.setdefault(scheme_name, dict(scheme))
        return [{scheme_name: []}]


def _infer_request_model(handler) -> Any:
    signature = inspect.signature(handler)
    resolved_hints = get_callable_type_hints(handler)
    for parameter in signature.parameters.values():
        if parameter.name in {"request", "app"}:
            continue
        if parameter.default is not inspect.Signature.empty:
            continue
        annotation = resolved_hints.get(parameter.name, parameter.annotation)
        if annotation is inspect.Signature.empty:
            continue
        return annotation
    return None


def _infer_response_model(handler) -> Any:
    annotation = get_callable_type_hints(handler).get("return", inspect.signature(handler).return_annotation)
    if annotation is inspect.Signature.empty:
        return None
    if _is_response_annotation(annotation):
        return None
    return annotation


def _is_response_annotation(annotation: Any) -> bool:
    try:
        return inspect.isclass(annotation) and issubclass(annotation, (Response, TextResponse, JsonResponse, StreamingResponse))
    except TypeError:
        return False


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


def _escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _serialize_response_doc(status_code: int, response_doc: Any) -> dict[str, Any]:
    if not isinstance(response_doc, dict):
        raise TypeError("Route response docs for %s must be dictionaries." % status_code)

    if any(key in response_doc for key in {"schema", "description", "media_type"}):
        schema = response_doc.get("schema")
        media_type = response_doc.get("media_type", "application/json")
        description = response_doc.get("description") or "HTTP %s response" % status_code
    else:
        schema = response_doc
        media_type = "application/json"
        description = "HTTP %s response" % status_code

    result: dict[str, Any] = {"description": description}
    if schema is not None:
        result["content"] = {
            media_type: {
                "schema": dict(schema),
            }
        }
    return result
