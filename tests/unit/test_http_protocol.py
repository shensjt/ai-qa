from __future__ import annotations

import socket
import threading
import time
from typing import Any

import pytest

from ai_qa.execution.http_protocol import (
    HttpConnection,
    HttpProtocolError,
    HttpTimeout,
    build_request,
)


def _response(
    status: int = 200,
    body: bytes = b"hello",
    reason: str = "OK",
    headers: dict[str, str] | None = None,
) -> bytes:
    merged = {"Content-Length": str(len(body)), "Connection": "keep-alive"}
    merged.update(headers or {})
    lines = [f"HTTP/1.1 {status} {reason}"]
    lines.extend(f"{name}: {value}" for name, value in merged.items())
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body


class FakeServer:
    def __init__(
        self,
        responses: list[bytes],
        *,
        respond_per_request: bool = False,
        silent: bool = False,
    ) -> None:
        self._responses = responses
        self._respond_per_request = respond_per_request
        self._silent = silent
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(1)
        self.port = self._socket.getsockname()[1]
        self.received = b""
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            connection, _ = self._socket.accept()
        except OSError:
            return
        try:
            if self._silent:
                time.sleep(1.0)
                return
            if self._respond_per_request:
                for response in self._responses:
                    self._read_request(connection)
                    connection.sendall(response)
            else:
                self._read_request(connection)
                for response in self._responses:
                    connection.sendall(response)
        finally:
            connection.close()

    def _read_request(self, connection: socket.socket) -> None:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = connection.recv(4096)
            if not chunk:
                break
            data += chunk
        self.received += data

    def close(self) -> None:
        self._socket.close()


@pytest.fixture
def server_factory() -> list[FakeServer]:
    servers: list[FakeServer] = []

    def make(responses: list[bytes], **kwargs: bool) -> FakeServer:
        server = FakeServer(responses, **kwargs)
        servers.append(server)
        return server

    yield make
    for server in servers:
        server.close()


def _get(host: str, port: int, target: str = "/") -> HttpConnection:
    return HttpConnection(host, port, connect_timeout=1.0, read_timeout=1.0)


def test_build_request_adds_host_and_validates() -> None:
    request = build_request("GET", "/a?b=1", "127.0.0.1:8080")

    assert request == b"GET /a?b=1 HTTP/1.1\r\nHost: 127.0.0.1:8080\r\n\r\n"
    with pytest.raises(ValueError):
        build_request("GET", "no-slash", "host")
    with pytest.raises(ValueError):
        build_request("GET", "/ok", "host", [("X", "bad\r\nvalue")])


def test_reads_single_response(server_factory: Any) -> None:
    make = server_factory
    server = make([_response(body=b"payload", headers={"X-Test": "yes"})])

    with _get("127.0.0.1", server.port) as connection:
        connection.send(build_request("GET", "/", "127.0.0.1"))
        response = connection.read_response()

    assert response.status == 200
    assert response.reason == "OK"
    assert response.body == b"payload"
    assert response.header("x-test") == "yes"
    assert response.raw.startswith(b"HTTP/1.1 200 OK\r\n")
    assert response.raw.endswith(b"payload")


def test_reads_fragmented_response(server_factory: Any) -> None:
    make = server_factory
    server = make([_response(body=b"0123456789")])

    with _get("127.0.0.1", server.port) as connection:
        connection.send(build_request("GET", "/", "127.0.0.1"))
        response = connection.read_response()

    assert response.body == b"0123456789"
    assert response.header("content-length") == "10"


def test_head_does_not_consume_body(server_factory: Any) -> None:
    make = server_factory
    head = _response(body=b"", headers={"Content-Length": "5", "Connection": "keep-alive"})
    followup = _response(body=b"ok")
    server = make([head, followup], respond_per_request=True)

    with _get("127.0.0.1", server.port) as connection:
        connection.send(build_request("HEAD", "/", "127.0.0.1"))
        head_response = connection.read_response(expect_body=False)
        connection.send(build_request("GET", "/healthz", "127.0.0.1"))
        follow = connection.read_response()

    assert head_response.status == 200
    assert head_response.body == b""
    assert head_response.header("content-length") == "5"
    assert follow.body == b"ok"


def test_sequential_keep_alive(server_factory: Any) -> None:
    make = server_factory
    server = make([_response(body=b"one"), _response(body=b"two")], respond_per_request=True)

    with _get("127.0.0.1", server.port) as connection:
        connection.send(build_request("GET", "/", "127.0.0.1"))
        first = connection.read_response()
        connection.send(build_request("GET", "/healthz", "127.0.0.1"))
        second = connection.read_response()

    assert first.body == b"one"
    assert second.body == b"two"


def test_pipelined_responses_are_split_on_boundaries(
    server_factory: Any,
) -> None:
    make = server_factory
    server = make([_response(body=b"one"), _response(body=b"two")])
    request = build_request("GET", "/", "127.0.0.1")

    with _get("127.0.0.1", server.port) as connection:
        connection.send(request + request)
        first = connection.read_response()
        second = connection.read_response()

    assert first.body == b"one"
    assert second.body == b"two"
    assert server.received.count(b"GET / HTTP/1.1") == 2


def test_missing_content_length_is_protocol_error(server_factory: Any) -> None:
    make = server_factory
    raw = b"HTTP/1.1 200 OK\r\nConnection: keep-alive\r\n\r\n"
    server = make([raw])

    with _get("127.0.0.1", server.port) as connection:
        connection.send(build_request("GET", "/", "127.0.0.1"))
        with pytest.raises(HttpProtocolError):
            connection.read_response()


def test_invalid_status_line_is_protocol_error(server_factory: Any) -> None:
    make = server_factory
    server = make([b"NOT-HTTP\r\n\r\n"])

    with _get("127.0.0.1", server.port) as connection:
        connection.send(build_request("GET", "/", "127.0.0.1"))
        with pytest.raises(HttpProtocolError):
            connection.read_response()


def test_silent_server_times_out(server_factory: Any) -> None:
    make = server_factory
    server = make([_response()], silent=True)

    connection = HttpConnection("127.0.0.1", server.port, connect_timeout=1.0, read_timeout=0.2)
    try:
        connection.send(build_request("GET", "/", "127.0.0.1"))
        with pytest.raises(HttpTimeout):
            connection.read_response()
    finally:
        connection.close()
