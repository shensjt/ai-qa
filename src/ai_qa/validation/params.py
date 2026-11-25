"""Catalog 参数类型与取值校验（实现规格 §9.1 / §13.1）。

HTTP request-target 与文件系统路径是两种输入：普通用例的 http_path 默认拒绝
路径穿越与编码穿越；只有 attack_path 类型可以引用 Catalog 中显式允许的攻击串。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

MAX_REQUEST_TARGET_LENGTH = 2048

ParamType = Literal["http_path", "attack_path", "integer_list"]


class ParamSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ParamType
    default: JsonValue = None
    description: str = ""
    min_items: int | None = Field(default=None, ge=0)
    max_items: int | None = Field(default=None, ge=0)
    min_value: int | None = None
    max_value: int | None = None
    allowed_values: list[str] | None = None


def _has_control_characters(value: str) -> bool:
    return any(ord(char) < 0x21 or ord(char) == 0x7F for char in value)


def validate_http_path(value: str) -> str | None:
    """普通 request-target 校验；返回错误信息或 None。"""
    if not value.startswith("/"):
        return "request-target 必须以 / 开头"
    if len(value) > MAX_REQUEST_TARGET_LENGTH:
        return f"request-target 长度不能超过 {MAX_REQUEST_TARGET_LENGTH}"
    if _has_control_characters(value):
        return "request-target 不得包含原始空格或控制字符"
    if "\\" in value:
        return "request-target 不得包含反斜杠"
    if "://" in value:
        return "request-target 不得包含 scheme"
    lowered = value.lower()
    if "%2e" in lowered or "%2f" in lowered or "%5c" in lowered:
        return "普通用例不允许编码的路径穿越字符"
    path_only = value.split("?", 1)[0].split("#", 1)[0]
    if any(segment == ".." for segment in path_only.split("/")):
        return "普通用例不允许 .. 路径段"
    return None


def validate_attack_path(value: str, allowed_values: list[str] | None) -> str | None:
    """攻击路径必须来自 Catalog 白名单，且仍拒绝控制字符。"""
    if _has_control_characters(value):
        return "攻击路径不得包含原始空格或控制字符"
    if len(value) > MAX_REQUEST_TARGET_LENGTH:
        return f"攻击路径长度不能超过 {MAX_REQUEST_TARGET_LENGTH}"
    if not allowed_values:
        return "attack_path 未配置 allowed_values"
    if value not in allowed_values:
        return f"攻击路径不在 Catalog 白名单中: {value}"
    return None


def validate_integer_list(spec: ParamSpec, value: Any) -> str | None:
    if not isinstance(value, list) or any(
        isinstance(item, bool) or not isinstance(item, int) for item in value
    ):
        return "必须是整数列表"
    if spec.min_items is not None and len(value) < spec.min_items:
        return f"元素个数不能少于 {spec.min_items}"
    if spec.max_items is not None and len(value) > spec.max_items:
        return f"元素个数不能超过 {spec.max_items}"
    for item in value:
        if spec.min_value is not None and item < spec.min_value:
            return f"元素不能小于 {spec.min_value}"
        if spec.max_value is not None and item > spec.max_value:
            return f"元素不能大于 {spec.max_value}"
    return None


def validate_param_value(spec: ParamSpec, value: Any) -> str | None:
    """按 ParamSpec 校验单个参数值，返回错误信息或 None。"""
    if spec.type == "http_path":
        if not isinstance(value, str):
            return "必须是字符串"
        return validate_http_path(value)
    if spec.type == "attack_path":
        if not isinstance(value, str):
            return "必须是字符串"
        return validate_attack_path(value, spec.allowed_values)
    return validate_integer_list(spec, value)


def validate_param_spec(spec: ParamSpec) -> str | None:
    """Catalog 自身的完整性：默认值必须存在且合法。"""
    if spec.default is None:
        return "参数必须声明 default"
    if spec.type == "attack_path" and not spec.allowed_values:
        return "attack_path 必须声明 allowed_values"
    if spec.type != "attack_path" and spec.allowed_values:
        return "allowed_values 只适用于 attack_path"
    if spec.type == "integer_list":
        if (
            spec.min_items is not None
            and spec.max_items is not None
            and spec.min_items > spec.max_items
        ):
            return "min_items 不能大于 max_items"
        if (
            spec.min_value is not None
            and spec.max_value is not None
            and spec.min_value > spec.max_value
        ):
            return "min_value 不能大于 max_value"
    return validate_param_value(spec, spec.default)
