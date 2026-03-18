# Testing

## Run the suite

`tasgi` uses `unittest`.

```bash
python3 -m unittest discover -s tests -v
```

## Coverage

```bash
python3 -m pip install coverage
coverage run -m unittest discover -s tests
coverage report -m
coverage html
```

## Example app-level test

```python
import unittest

from tasgi import TasgiApp, TextResponse
from tasgi.asgi_server import ASGIServer


def build_get_request(path: str) -> bytes:
    return f"GET {path} HTTP/1.1\r\nHost: example.test\r\n\r\n".encode("ascii")


class AppTests(unittest.IsolatedAsyncioTestCase):
    async def test_home(self) -> None:
        app = TasgiApp()

        @app.route.get("/")
        async def home(request):
            return TextResponse("home")

        try:
            response = await ASGIServer(app).handle_raw_request(build_get_request("/"))
        finally:
            await app.close()

        self.assertIn(b"HTTP/1.1 200 OK", response)
        self.assertTrue(response.endswith(b"\r\n\r\nhome"))
```

## What the suite should cover

- routing and 404/405 behavior
- request and response helpers
- ASGI runtime correctness
- sync thread execution
- HTTP/2 prototype behavior
- WebSocket flow
- OpenAPI generation
- auth behavior
- streaming completion
