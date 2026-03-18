"""Authentication and authorization tests."""

from __future__ import annotations

import asyncio
import base64
import unittest

from support import build_get_request
from tasgi import (
    APIKeyBackend,
    BearerTokenBackend,
    BasicAuthBackend,
    Identity,
    JsonResponse,
    RequireScope,
    TasgiApp,
    TextResponse,
)
from tasgi.asgi_server import ASGIServer


class TasgiAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_global_auth_backend_populates_request_auth(self) -> None:
        def validate_token(token: str):
            if token == "good-token":
                return Identity(subject="alice", scopes=frozenset({"profile"}))
            return None

        app = TasgiApp(auth_backend=BearerTokenBackend(validate_token))

        @app.route.get("/public", auth=False)
        async def public_route(request) -> TextResponse:
            return TextResponse("public")

        @app.route.get("/me", auth=True)
        async def me(request) -> JsonResponse:
            assert request.auth is not None
            assert request.user is not None
            return JsonResponse(
                {
                    "subject": request.user.subject,
                    "backend": request.auth.backend,
                    "authenticated": request.auth.is_authenticated,
                }
            )

        try:
            public_response, unauthorized_response, authorized_response = await asyncio.gather(
                ASGIServer(app).handle_raw_request(build_get_request("/public")),
                ASGIServer(app).handle_raw_request(build_get_request("/me")),
                ASGIServer(app).handle_raw_request(
                    build_get_request("/me").replace(
                        b"\r\n\r\n",
                        b"\r\nAuthorization: Bearer good-token\r\n\r\n",
                        1,
                    )
                ),
            )
        finally:
            await app.close()

        self.assertTrue(public_response.endswith(b"\r\n\r\npublic"))
        self.assertIn(b"HTTP/1.1 401 Unauthorized", unauthorized_response)
        self.assertIn(b'"subject": "alice"', authorized_response)
        self.assertIn(b'"backend": "bearer"', authorized_response)
        self.assertIn(b'"authenticated": true', authorized_response)

    async def test_auth_policy_can_require_scope(self) -> None:
        def validate_token(token: str):
            if token == "user-token":
                return {"subject": "alice", "scopes": ["profile"]}
            if token == "admin-token":
                return {"subject": "admin", "scopes": ["admin"]}
            return None

        app = TasgiApp(auth_backend=BearerTokenBackend(validate_token))

        @app.route.get("/admin", auth=RequireScope("admin"))
        async def admin_route(request) -> TextResponse:
            assert request.identity is not None
            return TextResponse(request.identity.subject)

        try:
            forbidden_response, ok_response = await asyncio.gather(
                ASGIServer(app).handle_raw_request(
                    build_get_request("/admin").replace(
                        b"\r\n\r\n",
                        b"\r\nAuthorization: Bearer user-token\r\n\r\n",
                        1,
                    )
                ),
                ASGIServer(app).handle_raw_request(
                    build_get_request("/admin").replace(
                        b"\r\n\r\n",
                        b"\r\nAuthorization: Bearer admin-token\r\n\r\n",
                        1,
                    )
                ),
            )
        finally:
            await app.close()

        self.assertIn(b"HTTP/1.1 403 Forbidden", forbidden_response)
        self.assertTrue(ok_response.endswith(b"\r\n\r\nadmin"))

    async def test_route_can_override_auth_backend(self) -> None:
        app = TasgiApp(
            auth_backend=BearerTokenBackend(
                lambda token: Identity(subject="global") if token == "global-token" else None
            )
        )
        api_key_backend = APIKeyBackend(
            lambda key: Identity(subject="service") if key == "service-key" else None
        )

        @app.route.get("/service", auth=True, auth_backend=api_key_backend)
        async def service_route(request) -> TextResponse:
            assert request.identity is not None
            return TextResponse(request.identity.subject)

        try:
            wrong_backend_response, correct_backend_response = await asyncio.gather(
                ASGIServer(app).handle_raw_request(
                    build_get_request("/service").replace(
                        b"\r\n\r\n",
                        b"\r\nAuthorization: Bearer global-token\r\n\r\n",
                        1,
                    )
                ),
                ASGIServer(app).handle_raw_request(
                    build_get_request("/service").replace(
                        b"\r\n\r\n",
                        b"\r\nX-API-Key: service-key\r\n\r\n",
                        1,
                    )
                ),
            )
        finally:
            await app.close()

        self.assertIn(b"HTTP/1.1 401 Unauthorized", wrong_backend_response)
        self.assertTrue(correct_backend_response.endswith(b"\r\n\r\nservice"))

    async def test_basic_auth_backend_accepts_valid_credentials(self) -> None:
        backend = BasicAuthBackend(
            lambda username, password: Identity(subject=username) if password == "secret" else None
        )
        app = TasgiApp(auth_backend=backend)

        @app.route.get("/basic", auth=True)
        async def basic_route(request) -> TextResponse:
            return TextResponse(request.identity.subject)

        encoded = base64.b64encode(b"alice:secret").decode("ascii")
        try:
            response = await ASGIServer(app).handle_raw_request(
                build_get_request("/basic").replace(
                    b"\r\n\r\n",
                    f"\r\nAuthorization: Basic {encoded}\r\n\r\n".encode("ascii"),
                    1,
                )
            )
        finally:
            await app.close()

        self.assertTrue(response.endswith(b"\r\n\r\nalice"))
