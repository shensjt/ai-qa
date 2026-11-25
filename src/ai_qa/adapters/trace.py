"""MCP 协议级 trace（实现规格 §12.1 / §17.2）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai_qa.domain.models import new_id, utc_now
from ai_qa.log import redact_mapping


class JsonlTrace:
    """每行一个 JSON 事件；字段脱敏后落盘。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def event(self, event: str, **fields: Any) -> None:
        payload: dict[str, Any] = {
            "event_id": new_id("evt"),
            "ts": utc_now().isoformat(),
            "event": event,
            **redact_mapping(fields),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str))
            handle.write("\n")
