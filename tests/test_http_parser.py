"""Tests for the tiny HTTP parser."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from tasgi.http_parser import HTTPParseError, parse_http_request


class HTTPParserTests(unittest.TestCase):
    def test_valid_get_request(self) -> None:
        request = parse_http_request(
            b"GET /hello HTTP/1.1\r\nHost: example.test\r\n\r\n"
        )
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.path, "/hello")
        self.assertEqual(request.query_string, b"")
        self.assertEqual(request.body, b"")
        self.assertEqual(request.headers, [(b"host", b"example.test")])

    def test_valid_post_request_with_body(self) -> None:
        request = parse_http_request(
            b"POST /submit HTTP/1.1\r\nHost: example.test\r\nContent-Length: 5\r\n\r\nhello"
        )
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.path, "/submit")
        self.assertEqual(request.body, b"hello")

    def test_malformed_request_line(self) -> None:
        with self.assertRaisesRegex(HTTPParseError, "Malformed HTTP request line"):
            parse_http_request(b"GET /missing-version\r\nHost: example.test\r\n\r\n")

    def test_malformed_headers(self) -> None:
        with self.assertRaisesRegex(HTTPParseError, "Malformed HTTP header line"):
            parse_http_request(b"GET / HTTP/1.1\r\nHost example.test\r\n\r\n")

    def test_empty_body(self) -> None:
        request = parse_http_request(
            b"POST /empty HTTP/1.1\r\nContent-Length: 0\r\n\r\n"
        )
        self.assertEqual(request.body, b"")

    def test_query_string_parsing(self) -> None:
        request = parse_http_request(
            b"GET /items?name=tasgi&lang=py HTTP/1.1\r\nHost: example.test\r\n\r\n"
        )
        self.assertEqual(request.path, "/items")
        self.assertEqual(request.query_string, b"name=tasgi&lang=py")


if __name__ == "__main__":
    unittest.main()
