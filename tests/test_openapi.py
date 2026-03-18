"""OpenAPI and docs generation tests."""

from __future__ import annotations

from dataclasses import dataclass
import unittest

from support import build_get_request, build_post_request
from tasgi import (
    APIKeyBackend,
    BearerTokenBackend,
    BasicAuthBackend,
    Identity,
    JsonResponse,
    TasgiApp,
    TextResponse,
    THREAD_EXECUTION,
)
from tasgi.auth.base import AuthBackend
from tasgi.routing import Router
from tasgi.asgi_server import ASGIServer


class TasgiDocsTests(unittest.IsolatedAsyncioTestCase):
    def test_openapi_schema_collects_route_metadata_and_registered_schemas(self) -> None:
        app = TasgiApp()
        app.configure_docs(title="Demo API", version="1.2.0", description="Demo docs")

        @app.route.get(
            "/users/{id}",
            summary="Get user",
            description="Return one user",
            tags=["users"],
            operation_id="getUser",
        )
        async def get_user(request) -> JsonResponse:
            return JsonResponse({"id": request.route_params["id"]})

        @app.route.post("/users", summary="Create user")
        def create_user(request) -> JsonResponse:
            return JsonResponse({"created": True}, status_code=201)

        @app.websocket("/ws")
        async def websocket_route(websocket) -> None:
            await websocket.accept()
            await websocket.close()

        app.register_request_schema(
            "/users",
            "POST",
            {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
            description="User create payload",
        )
        app.register_response_schema(
            "/users/{id}",
            "GET",
            200,
            {"type": "object", "properties": {"id": {"type": "string"}}},
            description="User payload",
        )
        app.register_response_schema(
            "/users",
            "POST",
            201,
            {"type": "object", "properties": {"created": {"type": "boolean"}}},
        )

        document = app.openapi_schema()
        get_operation = document["paths"]["/users/{id}"]["get"]
        post_operation = document["paths"]["/users"]["post"]

        self.assertEqual(document["openapi"], "3.1.0")
        self.assertEqual(document["info"]["title"], "Demo API")
        self.assertEqual(document["info"]["version"], "1.2.0")
        self.assertEqual(document["info"]["description"], "Demo docs")
        self.assertNotIn("/ws", document["paths"])
        self.assertEqual(get_operation["summary"], "Get user")
        self.assertEqual(get_operation["description"], "Return one user")
        self.assertEqual(get_operation["tags"], ["users"])
        self.assertEqual(get_operation["operationId"], "getUser")
        self.assertEqual(get_operation["parameters"][0]["name"], "id")
        self.assertEqual(get_operation["responses"]["200"]["description"], "User payload")
        self.assertEqual(
            get_operation["responses"]["200"]["content"]["application/json"]["schema"]["type"],
            "object",
        )
        self.assertEqual(post_operation["summary"], "Create user")
        self.assertTrue(post_operation["requestBody"]["required"])
        self.assertEqual(post_operation["requestBody"]["description"], "User create payload")
        self.assertEqual(
            post_operation["requestBody"]["content"]["application/json"]["schema"]["required"],
            ["name"],
        )
        self.assertEqual(post_operation["responses"]["201"]["description"], "HTTP 201 response")
        self.assertEqual(post_operation["x-tasgi-execution"], THREAD_EXECUTION)

    def test_openapi_schema_defaults_to_success_response_without_explicit_docs(self) -> None:
        app = TasgiApp()

        @app.route.get("/")
        async def home(request) -> TextResponse:
            return TextResponse("home")

        document = app.openapi_schema()
        self.assertEqual(
            document["paths"]["/"]["get"]["responses"],
            {"200": {"description": "Successful Response"}},
        )

    async def test_builtin_openapi_and_docs_routes_work_from_config(self) -> None:
        app = TasgiApp(docs=True, title="Demo Docs", version="2.0.0")

        @app.route.get("/", summary="Home", response_model=str)
        async def home(request) -> str:
            return "home"

        try:
            openapi_response, docs_response = await ASGIServer(app).handle_raw_request(
                build_get_request("/openapi.json")
            ), await ASGIServer(app).handle_raw_request(build_get_request("/docs"))
        finally:
            await app.close()

        self.assertIn(b'"title": "Demo Docs"', openapi_response)
        self.assertIn(b'"/": {"get":', openapi_response)
        self.assertIn(b"swagger-ui", docs_response)
        self.assertIn(b"/openapi.json", docs_response)

    def test_openapi_infers_request_and_response_models(self) -> None:
        @dataclass
        class EchoIn:
            message: str

        @dataclass
        class EchoOut:
            echoed: str

        app = TasgiApp()

        @app.route.post(
            "/echo",
            summary="Echo message",
            tags=["demo"],
            request_model=EchoIn,
            response_model=EchoOut,
            status_code=201,
        )
        def echo(request, body: EchoIn) -> EchoOut:
            return EchoOut(echoed=body.message)

        document = app.openapi_schema()
        operation = document["paths"]["/echo"]["post"]
        self.assertEqual(operation["summary"], "Echo message")
        self.assertEqual(operation["tags"], ["demo"])
        self.assertEqual(
            operation["requestBody"]["content"]["application/json"]["schema"]["required"],
            ["message"],
        )
        self.assertEqual(
            operation["responses"]["201"]["content"]["application/json"]["schema"]["properties"]["echoed"]["type"],
            "string",
        )

    def test_router_level_tags_and_error_responses_flow_into_openapi(self) -> None:
        app = TasgiApp()
        router = Router(
            tags=["users"],
            responses={
                404: {
                    "description": "User not found",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "detail": {"type": "string"},
                        },
                        "required": ["detail"],
                    },
                }
            },
        )

        @router.get("/users/{id}", summary="Get one user", tags=["detail"], response_model=dict[str, str])
        async def get_user(request) -> dict[str, str]:
            return {"id": request.route_params["id"]}

        app.include_router(router, prefix="/api")

        document = app.openapi_schema()
        operation = document["paths"]["/api/users/{id}"]["get"]

        self.assertEqual(operation["summary"], "Get one user")
        self.assertEqual(operation["tags"], ["users", "detail"])
        self.assertEqual(operation["responses"]["404"]["description"], "User not found")
        self.assertEqual(
            operation["responses"]["404"]["content"]["application/json"]["schema"]["properties"]["detail"]["type"],
            "string",
        )
        self.assertEqual(
            operation["responses"]["200"]["content"]["application/json"]["schema"]["additionalProperties"]["type"],
            "string",
        )

    def test_openapi_includes_auth_security_schemes_automatically(self) -> None:
        app = TasgiApp(
            auth_backend=BearerTokenBackend(
                lambda token: Identity(subject=token),
                bearer_format="JWT",
                description="Demo bearer auth",
            )
        )
        api_key_backend = APIKeyBackend(
            lambda key: Identity(subject=key),
            header_name="x-service-key",
            description="Service API key",
        )
        basic_backend = BasicAuthBackend(
            lambda username, password: Identity(subject=username) if password == "secret" else None,
            description="Basic auth demo",
        )

        class CustomBackend(AuthBackend):
            name = "custom"

            def authenticate(self, request):
                del request
                return None

            def openapi_security_scheme_name(self) -> str:
                return "customGateway"

            def openapi_security_scheme(self):
                return {
                    "type": "apiKey",
                    "in": "header",
                    "name": "x-custom-auth",
                    "description": "Custom gateway auth",
                }

        @app.route.get("/public", auth=False)
        async def public_route(request) -> TextResponse:
            return TextResponse("public")

        @app.route.get("/me", auth=True)
        async def me(request) -> TextResponse:
            return TextResponse("me")

        @app.route.get("/service", auth=True, auth_backend=api_key_backend)
        async def service(request) -> TextResponse:
            return TextResponse("service")

        @app.route.get("/basic", auth=True, auth_backend=basic_backend)
        async def basic(request) -> TextResponse:
            return TextResponse("basic")

        @app.route.get("/custom", auth=True, auth_backend=CustomBackend())
        async def custom(request) -> TextResponse:
            return TextResponse("custom")

        document = app.openapi_schema()
        schemes = document["components"]["securitySchemes"]

        self.assertEqual(schemes["bearerAuth"]["type"], "http")
        self.assertEqual(schemes["bearerAuth"]["scheme"], "bearer")
        self.assertEqual(schemes["bearerAuth"]["bearerFormat"], "JWT")
        self.assertEqual(schemes["apiKeyAuth"]["type"], "apiKey")
        self.assertEqual(schemes["apiKeyAuth"]["name"], "x-service-key")
        self.assertEqual(schemes["basicAuth"]["scheme"], "basic")
        self.assertEqual(schemes["customGateway"]["name"], "x-custom-auth")
        self.assertEqual(document["paths"]["/public"]["get"]["security"], [])
        self.assertEqual(document["paths"]["/me"]["get"]["security"], [{"bearerAuth": []}])
        self.assertEqual(document["paths"]["/service"]["get"]["security"], [{"apiKeyAuth": []}])
        self.assertEqual(document["paths"]["/basic"]["get"]["security"], [{"basicAuth": []}])
        self.assertEqual(document["paths"]["/custom"]["get"]["security"], [{"customGateway": []}])

    async def test_typed_body_parameter_and_model_return_are_coerced_automatically(self) -> None:
        @dataclass
        class EchoIn:
            message: str

        @dataclass
        class EchoOut:
            echoed: str

        app = TasgiApp()

        @app.route.post("/echo", request_model=EchoIn, response_model=EchoOut, status_code=201)
        def echo(request, body: EchoIn) -> EchoOut:
            self.assertIsInstance(body, EchoIn)
            return EchoOut(echoed=body.message)

        try:
            response = await ASGIServer(app).handle_raw_request(
                build_post_request("/echo", b'{"message":"hi"}')
            )
        finally:
            await app.close()

        self.assertIn(b"HTTP/1.1 201 Created", response)
        self.assertIn(b'{"echoed": "hi"}', response)
