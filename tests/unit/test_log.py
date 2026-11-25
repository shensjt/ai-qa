from __future__ import annotations

import json
import logging

from ai_qa.log import JsonFormatter, redact_mapping


def test_redact_mapping_recurses() -> None:
    data = {
        "api_key": "sk-real",
        "nested": {"Authorization": "Bearer x", "ok": 1},
        "items": [{"token": "t"}, "plain"],
        "run_id": "run_01",
    }

    redacted = redact_mapping(data)

    assert redacted["api_key"] == "***"
    assert redacted["nested"]["Authorization"] == "***"
    assert redacted["nested"]["ok"] == 1
    assert redacted["items"][0]["token"] == "***"
    assert redacted["items"][1] == "plain"
    assert redacted["run_id"] == "run_01"
    assert data["api_key"] == "sk-real"  # 不修改原对象


def test_json_formatter_includes_and_redacts_context() -> None:
    record = logging.LogRecord(
        name="ai_qa.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    record.context = {"run_id": "run_01", "api_key": "sk-real"}

    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "ai_qa.test"
    assert payload["message"] == "hello world"
    assert payload["run_id"] == "run_01"
    assert payload["api_key"] == "***"
    assert "ts" in payload
