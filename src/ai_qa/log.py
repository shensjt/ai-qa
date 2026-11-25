"""结构化日志与脱敏工具（实现规格 §17.1 / §17.2）。

约定：需要结构化字段时使用 ``logger.info("msg", extra={"context": {...}})``；
JsonFormatter 会把 context 合并进输出并递归脱敏敏感键。
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

_SECRET_KEY_PATTERN = re.compile(
    r"(api[_-]?key|access[_-]?key|token|secret|password|authorization|credential)",
    re.IGNORECASE,
)
_REDACTED = "***"


def redact_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    """递归复制并脱敏映射中的敏感键。"""
    return {key: _redact_value(key, value) for key, value in data.items()}


def _redact_value(key: str, value: Any) -> Any:
    if _SECRET_KEY_PATTERN.search(key):
        return _REDACTED
    if isinstance(value, Mapping):
        return redact_mapping(value)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [_redact_value(key, item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    """单行 JSON 日志；不记录异常堆栈正文，只记录异常类型。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        context = getattr(record, "context", None)
        if isinstance(context, Mapping):
            payload.update(redact_mapping(context))
        if record.exc_info is not None and record.exc_info[0] is not None:
            payload["exc_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    """配置根日志：JSON 输出到 stderr。"""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
