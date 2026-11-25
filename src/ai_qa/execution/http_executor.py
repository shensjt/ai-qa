"""HTTP 黑盒用例执行器（实现规格 §13.2）。

Executor 只产生 CaseFacts：状态、原始报文、摘要、连接行为。
不放 expected/rule 判断，也不根据产品行为改写 execution_status。
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from typing import Any

from ai_qa.application.ports import ExecutionContext
from ai_qa.domain.errors import AiQaError, ErrorCode, ErrorInfo
from ai_qa.domain.models import utc_now
from ai_qa.domain.plans import ResolvedPlanItem
from ai_qa.domain.results import ArtifactRef, CaseFacts
from ai_qa.execution.http_protocol import (
    HttpConnection,
    HttpProtocolError,
    HttpResponse,
    HttpTimeout,
    build_request,
)

_SELECTED_HEADERS = (
    "allow",
    "connection",
    "content-length",
    "content-type",
    "etag",
    "last-modified",
)
_MAX_PREVIEW_CHARS = 300


Handler = Callable[
    [ResolvedPlanItem, ExecutionContext],
    tuple[dict[str, Any], dict[str, Any], list[ArtifactRef]],
]


class HttpExecutor:
    kind = "http"

    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {
            "http.get_index": self._case_get_index,
            "http.head": self._case_head,
            "http.keep_alive": self._case_keep_alive,
            "http.pipelined": self._case_pipelined,
            "http.split_request": self._case_split_request,
            "http.path_traversal": self._case_path_traversal,
        }

    def execute(self, item: ResolvedPlanItem, context: ExecutionContext) -> CaseFacts:
        started = utc_now()
        request_facts: dict[str, Any] = {}
        observed: dict[str, Any] = {}
        artifacts: list[ArtifactRef] = []
        error: ErrorInfo | None = None

        try:
            handler = self._handlers.get(item.case_id)
            if handler is None:
                raise AiQaError(
                    ErrorCode.UNKNOWN_CASE,
                    f"HTTP Executor 未实现 case: {item.case_id}",
                )
            request_facts, observed, artifacts = handler(item, context)
        except HttpTimeout as exc:
            error = ErrorInfo(
                code=ErrorCode.CASE_TIMEOUT,
                message=f"用例执行超时: {exc}",
                cause_type=type(exc).__name__,
            )
        except TimeoutError as exc:
            error = ErrorInfo(
                code=ErrorCode.CASE_TIMEOUT,
                message=f"用例执行超时: {exc}",
                cause_type=type(exc).__name__,
            )
        except AiQaError as exc:
            error = exc.to_error_info()
        except (OSError, HttpProtocolError) as exc:
            error = ErrorInfo(
                code=ErrorCode.INTERNAL_ERROR,
                message=f"HTTP 交互失败: {exc}",
                cause_type=type(exc).__name__,
            )

        return CaseFacts(
            case_id=item.case_id,
            started_at=started,
            ended_at=utc_now(),
            target={"host": context.host, "port": context.port},
            request=request_facts,
            observed=observed,
            artifacts=artifacts,
            executor_error=error,
        )

    # ---------------------------------------------------------------- cases

    def _case_get_index(
        self, item: ResolvedPlanItem, context: ExecutionContext
    ) -> tuple[dict[str, Any], dict[str, Any], list[ArtifactRef]]:
        path = _path(item)
        request = build_request("GET", path, context.host_header)
        with self._connect(item, context) as connection:
            connection.send(request)
            response = connection.read_response()
        artifacts = self._write_exchange(context, item, "get", request, response)
        observed = _response_summary(response)
        observed["elapsed_ms"] = round(response.elapsed_ms, 3)
        return {"method": "GET", "path": path}, observed, artifacts

    def _case_head(
        self, item: ResolvedPlanItem, context: ExecutionContext
    ) -> tuple[dict[str, Any], dict[str, Any], list[ArtifactRef]]:
        path = _path(item)
        request = build_request("HEAD", path, context.host_header)
        artifacts: list[ArtifactRef] = []
        followup_status: int | None = None
        followup_error: str | None = None
        connection_reused = False
        buffered_after_head = 0

        with self._connect(item, context) as connection:
            connection.send(request)
            response = connection.read_response(expect_body=False)
            buffered_after_head = connection.buffered_bytes()
            artifacts.extend(self._write_exchange(context, item, "head", request, response))

            if response.close_requested:
                followup_error = "HEAD 响应声明 Connection: close"
            else:
                followup_request = build_request("GET", context.health_path, context.host_header)
                try:
                    connection.send(followup_request)
                    followup = connection.read_response()
                except (OSError, HttpProtocolError) as exc:
                    followup_error = str(exc)
                else:
                    followup_status = followup.status
                    connection_reused = True
                    artifacts.extend(
                        self._write_exchange(context, item, "followup", followup_request, followup)
                    )

        observed: dict[str, Any] = {
            "status": response.status,
            "reason": response.reason,
            "headers": _header_summary(response),
            "content_length_header": _int_or_none(response.header("content-length")),
            "body_bytes_read": len(response.body),
            "buffered_after_head": buffered_after_head,
            "followup_status": followup_status,
            "followup_error": followup_error,
            "connection_reused": connection_reused,
            "elapsed_ms": round(response.elapsed_ms, 3),
        }
        return (
            {"method": "HEAD", "path": path, "followup_path": context.health_path},
            observed,
            artifacts,
        )

    def _case_keep_alive(
        self, item: ResolvedPlanItem, context: ExecutionContext
    ) -> tuple[dict[str, Any], dict[str, Any], list[ArtifactRef]]:
        path = _path(item)
        first_request = build_request("GET", path, context.host_header)
        second_request = build_request("GET", context.health_path, context.host_header)
        artifacts: list[ArtifactRef] = []
        responses: list[dict[str, Any]] = []
        second_error: str | None = None

        with self._connect(item, context) as connection:
            connection.send(first_request)
            first = connection.read_response()
            artifacts.extend(
                self._write_exchange(context, item, "keepalive_1", first_request, first)
            )
            responses.append(_response_summary(first))

            if first.close_requested:
                second_error = "首个响应声明 Connection: close"
            else:
                try:
                    connection.send(second_request)
                    second = connection.read_response()
                except (OSError, HttpProtocolError) as exc:
                    second_error = str(exc)
                else:
                    artifacts.extend(
                        self._write_exchange(context, item, "keepalive_2", second_request, second)
                    )
                    responses.append(_response_summary(second))

        observed = {
            "responses": responses,
            "response_count": len(responses),
            "connection_reused": second_error is None and len(responses) == 2,
            "second_error": second_error,
        }
        return (
            {
                "method": "GET",
                "path": path,
                "second_path": context.health_path,
                "requests_on_same_connection": 2,
            },
            observed,
            artifacts,
        )

    def _case_pipelined(
        self, item: ResolvedPlanItem, context: ExecutionContext
    ) -> tuple[dict[str, Any], dict[str, Any], list[ArtifactRef]]:
        path = _path(item)
        first_request = build_request("GET", path, context.host_header)
        second_request = build_request("GET", context.health_path, context.host_header)
        artifacts: list[ArtifactRef] = []
        responses: list[dict[str, Any]] = []
        second_error: str | None = None

        with self._connect(item, context) as connection:
            connection.send(first_request + second_request)
            first = connection.read_response()
            responses.append(_response_summary(first))
            artifacts.extend(
                self._write_exchange(context, item, "pipelined_1", first_request, first)
            )
            try:
                second = connection.read_response()
            except (OSError, HttpProtocolError) as exc:
                second_error = str(exc)
            else:
                responses.append(_response_summary(second))
                artifacts.extend(
                    self._write_exchange(context, item, "pipelined_2", second_request, second)
                )

        observed = {
            "responses": responses,
            "response_count": len(responses),
            "second_error": second_error,
            "pipelined_in_one_send": True,
        }
        return (
            {"method": "GET", "path": path, "second_path": context.health_path},
            observed,
            artifacts,
        )

    def _case_split_request(
        self, item: ResolvedPlanItem, context: ExecutionContext
    ) -> tuple[dict[str, Any], dict[str, Any], list[ArtifactRef]]:
        path = _path(item)
        split_sizes = _split_sizes(item)
        request = build_request("GET", path, context.host_header)
        artifacts: list[ArtifactRef] = []

        with self._connect(item, context) as connection:
            connection.send(request)
            baseline = connection.read_response()
        artifacts.extend(self._write_exchange(context, item, "baseline", request, baseline))

        chunks: list[int] = []
        with self._connect(item, context) as connection:
            offset = 0
            index = 0
            while offset < len(request):
                size = split_sizes[index % len(split_sizes)]
                chunk = request[offset : offset + size]
                chunks.append(len(chunk))
                connection.send(chunk)
                offset += len(chunk)
                index += 1
                if offset < len(request) and context.split_delay_seconds > 0:
                    time.sleep(context.split_delay_seconds)
            split = connection.read_response()
        artifacts.extend(self._write_exchange(context, item, "split", request, split))

        observed = {
            "baseline": _response_summary(baseline),
            "split": _response_summary(split),
            "split_sizes": split_sizes,
            "chunk_sizes_used": chunks,
            "chunks_sent": len(chunks),
            "split_delay_ms": round(context.split_delay_seconds * 1000, 3),
        }
        return (
            {"method": "GET", "path": path, "split_sizes": split_sizes},
            observed,
            artifacts,
        )

    def _case_path_traversal(
        self, item: ResolvedPlanItem, context: ExecutionContext
    ) -> tuple[dict[str, Any], dict[str, Any], list[ArtifactRef]]:
        path = _path(item)
        request = build_request("GET", path, context.host_header, [("Connection", "close")])
        with self._connect(item, context) as connection:
            connection.send(request)
            response = connection.read_response()
        artifacts = self._write_exchange(context, item, "traversal", request, response)

        marker = context.secret_marker
        body_text = response.body.decode("utf-8", errors="replace")
        observed = {
            "status": response.status,
            "reason": response.reason,
            "headers": _header_summary(response),
            **_body_summary(response.body),
            "secret_marker_configured": marker is not None,
            "secret_leaked": marker is not None and marker in body_text,
            "secret_name_in_body": "outside_secret" in body_text.lower(),
            "elapsed_ms": round(response.elapsed_ms, 3),
        }
        return {"method": "GET", "path": path}, observed, artifacts

    # ------------------------------------------------------------- helpers

    def _connect(self, item: ResolvedPlanItem, context: ExecutionContext) -> HttpConnection:
        timeout = float(item.timeout_seconds)
        return HttpConnection(
            context.host,
            context.port,
            connect_timeout=min(2.0, timeout),
            read_timeout=timeout,
        )

    def _write_exchange(
        self,
        context: ExecutionContext,
        item: ResolvedPlanItem,
        label: str,
        request: bytes,
        response: HttpResponse,
    ) -> list[ArtifactRef]:
        safe_case = item.case_id.replace(".", "_")
        base = f"cases/{safe_case}.{label}"
        request_ref = context.artifact_store.write_bytes(
            context.run_id,
            f"{base}.request.txt",
            request,
            "http_request",
            mime="text/plain; charset=utf-8",
        )
        response_ref = context.artifact_store.write_bytes(
            context.run_id,
            f"{base}.response.bin",
            response.raw,
            "http_response",
            mime="application/octet-stream",
        )
        return [request_ref, response_ref]


def _path(item: ResolvedPlanItem) -> str:
    value = item.params.get("path", "/")
    if not isinstance(value, str):
        raise AiQaError(ErrorCode.PARAM_INVALID, f"{item.case_id} 的 path 参数必须是字符串")
    return value


def _split_sizes(item: ResolvedPlanItem) -> list[int]:
    value = item.params.get("split_sizes", [1, 3, 7])
    if not isinstance(value, list):
        raise AiQaError(
            ErrorCode.PARAM_INVALID,
            f"{item.case_id} 的 split_sizes 必须是整数列表",
        )
    sizes: list[int] = []
    for size in value:
        if isinstance(size, bool) or not isinstance(size, int):
            raise AiQaError(
                ErrorCode.PARAM_INVALID,
                f"{item.case_id} 的 split_sizes 必须是整数列表",
            )
        sizes.append(size)
    return sizes


def _header_summary(response: HttpResponse) -> dict[str, str]:
    return {name: response.headers[name] for name in _SELECTED_HEADERS if name in response.headers}


def _response_summary(response: HttpResponse) -> dict[str, Any]:
    return {
        "status": response.status,
        "reason": response.reason,
        "headers": _header_summary(response),
        **_body_summary(response.body),
    }


def _body_summary(body: bytes) -> dict[str, Any]:
    preview = body[:_MAX_PREVIEW_CHARS].decode("utf-8", errors="replace")
    return {
        "body_length": len(body),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "body_preview": preview,
    }


def _int_or_none(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None
