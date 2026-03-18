# WebSocket

## Register a WebSocket route

```python
from tasgi import TasgiApp

app = TasgiApp()

@app.websocket("/ws")
async def websocket_echo(websocket):
    await websocket.accept()
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            break
        if "text" in message:
            await websocket.send_text("echo:" + message["text"])
```

## Helper methods

The `WebSocket` wrapper supports:

- `await websocket.accept()`
- `await websocket.receive()`
- `await websocket.receive_text()`
- `await websocket.receive_bytes()`
- `await websocket.send_text(...)`
- `await websocket.send_bytes(...)`
- `await websocket.close(...)`

## Example with binary payloads

```python
@app.websocket("/bin")
async def websocket_binary(websocket):
    await websocket.accept()
    payload = await websocket.receive_bytes()
    await websocket.send_bytes(b"ack:" + payload)
```

## Notes

- WebSocket handlers must be `async`
- WebSocket routes are excluded from OpenAPI generation for now
- current support uses the HTTP/1.1 upgrade path
