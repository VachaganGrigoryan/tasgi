"""Compatibility tests for tasgi's WebSocket transport path."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from tasgi import TasgiApp
from tasgi.asgi_server import ASGIServer
from tasgi.wsproto import (
    OPCODE_BINARY,
    OPCODE_CLOSE,
    OPCODE_TEXT,
    build_accept_token,
    decode_close_payload,
)


def build_upgrade_request(path: str = "/ws", key: str = "dGhlIHNhbXBsZSBub25jZQ==") -> bytes:
    return (
        f"GET {path} HTTP/1.1\r\n"
        "Host: example.test\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    ).encode("ascii")


def build_client_frame(opcode: int, payload: bytes = b"", *, mask: bytes = b"test") -> bytes:
    first = 0x80 | opcode
    length = len(payload)
    masked_flag = 0x80
    if length < 126:
        header = bytes([first, masked_flag | length])
    elif length < 2**16:
        header = bytes([first, masked_flag | 126]) + length.to_bytes(2, "big")
    else:
        header = bytes([first, masked_flag | 127]) + length.to_bytes(8, "big")
    masked_payload = bytearray(payload)
    for index in range(len(masked_payload)):
        masked_payload[index] ^= mask[index % 4]
    return header + mask + bytes(masked_payload)


def parse_server_frames(data: bytes) -> list[tuple[int, bytes]]:
    frames: list[tuple[int, bytes]] = []
    index = 0
    while index < len(data):
        first = data[index]
        second = data[index + 1]
        opcode = first & 0x0F
        length = second & 0x7F
        header_length = 2
        if length == 126:
            length = int.from_bytes(data[index + 2 : index + 4], "big")
            header_length = 4
        elif length == 127:
            length = int.from_bytes(data[index + 2 : index + 10], "big")
            header_length = 10
        payload_start = index + header_length
        payload_end = payload_start + length
        frames.append((opcode, data[payload_start:payload_end]))
        index = payload_end
    return frames


class WebSocketServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_tasgi_app_websocket_echo_route(self) -> None:
        app = TasgiApp()

        @app.websocket("/ws")
        async def websocket_echo(websocket) -> None:
            await websocket.accept()
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                await websocket.send_text("echo:%s" % message["text"])

        raw_request = build_upgrade_request()
        incoming_frames = (
            build_client_frame(OPCODE_TEXT, b"hello")
            + build_client_frame(OPCODE_CLOSE, b"\x03\xe8")
        )
        try:
            response = await ASGIServer(app).handle_websocket_bytes(raw_request, incoming_frames)
        finally:
            await app.close()

        handshake, _, frame_bytes = response.partition(b"\r\n\r\n")
        self.assertIn(b"HTTP/1.1 101 Switching Protocols", handshake)
        self.assertIn(
            ("Sec-WebSocket-Accept: %s" % build_accept_token("dGhlIHNhbXBsZSBub25jZQ==")).encode("ascii"),
            handshake,
        )
        frames = parse_server_frames(frame_bytes)
        self.assertEqual(frames[0], (OPCODE_TEXT, b"echo:hello"))
        self.assertEqual(frames[1][0], OPCODE_CLOSE)
        self.assertEqual(decode_close_payload(frames[1][1])[0], 1000)

    async def test_raw_asgi_websocket_flow_emits_connect_receive_and_disconnect(self) -> None:
        events: list[str] = []

        async def app(scope, receive, send) -> None:
            self.assertEqual(scope["type"], "websocket")
            events.append("scope:%s" % scope["path"])
            connect = await receive()
            events.append(connect["type"])
            await send({"type": "websocket.accept"})
            message = await receive()
            events.append("text:%s" % message["text"])
            await send({"type": "websocket.send", "text": "pong"})
            disconnect = await receive()
            events.append("%s:%s" % (disconnect["type"], disconnect["code"]))

        raw_request = build_upgrade_request("/raw")
        incoming_frames = (
            build_client_frame(OPCODE_TEXT, b"ping")
            + build_client_frame(OPCODE_CLOSE, b"\x03\xe8")
        )
        response = await ASGIServer(app).handle_websocket_bytes(raw_request, incoming_frames)

        handshake, _, frame_bytes = response.partition(b"\r\n\r\n")
        self.assertIn(b"101 Switching Protocols", handshake)
        frames = parse_server_frames(frame_bytes)
        self.assertEqual(frames[0], (OPCODE_TEXT, b"pong"))
        self.assertEqual(events, ["scope:/raw", "websocket.connect", "text:ping", "websocket.disconnect:1000"])

    async def test_missing_websocket_route_is_rejected_before_handshake(self) -> None:
        app = TasgiApp()

        try:
            response = await ASGIServer(app).handle_websocket_bytes(build_upgrade_request("/missing"))
        finally:
            await app.close()

        self.assertIn(b"HTTP/1.1 403 Forbidden", response)

    async def test_binary_websocket_messages_round_trip(self) -> None:
        app = TasgiApp()

        @app.websocket("/bin")
        async def websocket_binary(websocket) -> None:
            await websocket.accept()
            payload = await websocket.receive_bytes()
            await websocket.send_bytes(b"ack:" + payload)

        raw_request = build_upgrade_request("/bin")
        incoming_frames = (
            build_client_frame(OPCODE_BINARY, b"\x01\x02")
            + build_client_frame(OPCODE_CLOSE, b"\x03\xe8")
        )
        try:
            response = await ASGIServer(app).handle_websocket_bytes(raw_request, incoming_frames)
        finally:
            await app.close()

        _, _, frame_bytes = response.partition(b"\r\n\r\n")
        frames = parse_server_frames(frame_bytes)
        self.assertEqual(frames[0], (OPCODE_BINARY, b"ack:\x01\x02"))


if __name__ == "__main__":
    unittest.main()
