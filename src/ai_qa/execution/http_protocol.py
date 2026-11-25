"""原始 HTTP/1.1 客户端与响应解析（实现规格 §13.1）。

只负责发送字节、解析响应、保留原始报文；不判断 case verdict。
"""

from __future__ import annotations

import contextlib
import socket
import time
from dataclasses import dataclass

MAX_BODY_BYTES = 16 * 1024 * 1024
MAX_HEADER_BYTES = 64 * 1024


class HttpProtocolError(Exception):
    """响应无法按 HTTP/1.x 解析，或连接在报文完成前关闭。"""


class HttpTimeout(HttpProtocolError):
    """等待响应超时；Executor 映射为 CASE_TIMEOUT。"""


@dataclass(frozen=True)
class HttpResponse:
    version: str
    status: int
    reason: str
    headers: dict[str, str]
    body: bytes
    raw: bytes
    elapsed_ms: float

    @property
    def close_requested(self) -> bool:
        return self.headers.get("connection", "").strip().lower() == "close"

    def header(self, name: str) -> str:
        return self.headers.get(name.strip().lower(), "")


def build_request(
    method: str,
    target: str,
    host: str,
    headers: list[tuple[str, str]] | None = None,
) -> bytes:
    """构造 HTTP/1.1 请求；request-target 只做最基本的字符检查。"""
    if not method or any(char in method for char in " \r\n"):
        raise ValueError(f"非法 method: {method!r}")
    if not target.startswith("/") or any(char in target for char in "\r\n\x00"):
        raise ValueError(f"非法 request-target: {target!r}")

    lines = [f"{method} {target} HTTP/1.1", f"Host: {host}"]
    for name, value in headers or []:
        if any(char in name + value for char in "\r\n"):
            raise ValueError(f"非法 header: {name!r}")
        lines.append(f"{name}: {value}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


class HttpConnection:
    """单连接读写；响应按 Content-Length 边界切分，支持同连接多响应。"""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        connect_timeout: float = 2.0,
        read_timeout: float = 5.0,
    ) -> None:
        self._sock = socket.create_connection((host, port), timeout=connect_timeout)
        self._sock.settimeout(read_timeout)
        self._buffer = b""
        self._closed = False

    def send(self, data: bytes) -> None:
        try:
            self._sock.sendall(data)
        except OSError:
            self._closed = True
            raise

    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def read_response(self, *, expect_body: bool = True) -> HttpResponse:
        started = time.monotonic()
        head = self._read_head()
        status_line, headers = self._parse_head(head)

        body = b""
        if (
            expect_body
            and status_line.status not in {204, 304}
            and not 100 <= status_line.status < 200
        ):
            length = self._content_length(headers)
            body = self._read_exact(length)

        elapsed_ms = (time.monotonic() - started) * 1000.0
        return HttpResponse(
            version=status_line.version,
            status=status_line.status,
            reason=status_line.reason,
            headers=headers,
            body=body,
            raw=head + body,
            elapsed_ms=elapsed_ms,
        )

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            with contextlib.suppress(OSError):
                self._sock.close()

    def __enter__(self) -> HttpConnection:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _read_head(self) -> bytes:
        while b"\r\n\r\n" not in self._buffer:
            if len(self._buffer) > MAX_HEADER_BYTES:
                raise HttpProtocolError("响应头超过上限")
            chunk = self._recv()
            self._buffer += chunk
            if len(self._buffer) > MAX_HEADER_BYTES:
                raise HttpProtocolError("响应头超过上限")

        head, self._buffer = self._buffer.split(b"\r\n\r\n", 1)
        return head + b"\r\n\r\n"

    def _recv(self) -> bytes:
        try:
            chunk = self._sock.recv(4096)
        except TimeoutError as exc:
            raise HttpTimeout("读取响应超时") from exc
        if not chunk:
            raise HttpProtocolError("连接在响应完成前关闭")
        return chunk

    def _parse_head(self, head: bytes) -> tuple[_StatusLine, dict[str, str]]:
        content = head[:-4] if head.endswith(b"\r\n\r\n") else head
        lines = content.split(b"\r\n")
        if not lines or not lines[0]:
            raise HttpProtocolError("缺少状态行")

        try:
            version, code, reason = lines[0].decode("latin-1").split(" ", 2)
        except ValueError as exc:
            raise HttpProtocolError("状态行格式错误") from exc
        if not version.startswith("HTTP/1."):
            raise HttpProtocolError(f"不支持的 HTTP 版本: {version}")
        try:
            status = int(code)
        except ValueError as exc:
            raise HttpProtocolError(f"非法状态码: {code}") from exc
        if not 100 <= status <= 599:
            raise HttpProtocolError(f"状态码超出范围: {status}")

        headers: dict[str, str] = {}
        for line in lines[1:]:
            name, separator, value = line.partition(b":")
            if not separator or not name.strip():
                raise HttpProtocolError(f"非法响应头: {line!r}")
            headers[name.decode("latin-1").strip().lower()] = value.decode("latin-1").strip()
        return _StatusLine(version=version, status=status, reason=reason), headers

    def _content_length(self, headers: dict[str, str]) -> int:
        raw = headers.get("content-length")
        if raw is None:
            raise HttpProtocolError("响应缺少 Content-Length")
        try:
            length = int(raw)
        except ValueError as exc:
            raise HttpProtocolError(f"非法 Content-Length: {raw!r}") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise HttpProtocolError(f"Content-Length 超出范围: {length}")
        return length

    def _read_exact(self, length: int) -> bytes:
        while len(self._buffer) < length:
            self._buffer += self._recv()
        body, self._buffer = self._buffer[:length], self._buffer[length:]
        return body


@dataclass(frozen=True)
class _StatusLine:
    version: str
    status: int
    reason: str
