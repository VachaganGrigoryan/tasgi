# Getting Started

## Install

From source:

```bash
pip install -e .
```

Run the test suite:

```bash
python3 -m unittest discover -s tests -v
```

## Hello world

```python
from tasgi import TasgiApp, TextResponse

app = TasgiApp(
    host="127.0.0.1",
    port=8000,
    docs=True,
    debug=True,
)

@app.route.get("/")
async def home(request):
    return TextResponse("hello from tasgi")
```

Run it:

```bash
tasgi
```

## Built-in docs

When `docs=True`, `tasgi` adds:

- `/openapi.json`
- `/docs`

Example:

```python
app = TasgiApp(
    docs=True,
    title="tasgi demo",
    version="0.1.0a1",
)
```

## Next pages

- [Routing](routing.md)
- [Handlers](handlers.md)
- [OpenAPI](openapi.md)
