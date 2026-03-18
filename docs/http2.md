# HTTP/2

## Current status

`tasgi` includes a native HTTP/2 prototype transport.

It is useful for:

- local validation
- transport experiments
- understanding stream-based request handling

It is not yet a full production HTTP/2 implementation.

## What it does

- validates the client preface
- handles SETTINGS and ACK flow
- maps each stream to its own ASGI HTTP scope
- routes responses back to the correct stream

## What is still limited

- full HPACK support is not the goal yet
- no full flow-control implementation
- no production-grade interoperability claim
- no full RFC-complete feature set

## Reading the protocol version

Handlers can inspect the request version:

```python
@app.route.get("/")
async def home(request):
    return {"http_version": request.http_version}
```

## Local testing

Use prior-knowledge cleartext HTTP/2 with `curl`:

```bash
curl --http2-prior-knowledge http://127.0.0.1:8000/
```

If the native HTTP/2 path rejects the request, run in debug mode to see the protocol error message.
