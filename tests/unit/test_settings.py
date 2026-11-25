from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ai_qa.domain.errors import AiQaError, ErrorCode
from ai_qa.settings import load_settings, resolve_config_path


def test_loads_example_config(example_config_path: Path) -> None:
    loaded = load_settings(example_config_path)

    assert loaded.config_path == example_config_path
    assert loaded.settings.schema_version == "1.0"
    assert loaded.settings.project.project_id == "cpp-webserver"
    assert loaded.settings.target.port == 18080
    assert loaded.settings.agent.max_plan_repairs == 2


def test_missing_config_file_reports_config_invalid(tmp_path: Path) -> None:
    with pytest.raises(AiQaError) as excinfo:
        load_settings(tmp_path / "missing.yaml")

    assert excinfo.value.code is ErrorCode.CONFIG_INVALID
    assert "不存在" in excinfo.value.message
    assert excinfo.value.details["path"].endswith("missing.yaml")


def test_non_mapping_yaml_rejected(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text("- just\n- a list\n", encoding="utf-8")

    with pytest.raises(AiQaError) as excinfo:
        load_settings(path)

    assert excinfo.value.code is ErrorCode.CONFIG_INVALID
    assert "映射" in excinfo.value.message


def test_broken_yaml_rejected(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text("schema_version: [unclosed\n", encoding="utf-8")

    with pytest.raises(AiQaError) as excinfo:
        load_settings(path)

    assert excinfo.value.code is ErrorCode.CONFIG_INVALID
    assert "YAML" in excinfo.value.message


def test_extra_field_rejected(example_data: dict[str, Any], write_config: Any) -> None:
    example_data["unexpected_section"] = {"x": 1}

    with pytest.raises(AiQaError) as excinfo:
        load_settings(write_config(example_data))

    issues = excinfo.value.details["issues"]
    assert any("unexpected_section" in issue["loc"] for issue in issues)


def test_unknown_schema_version_rejected(example_data: dict[str, Any], write_config: Any) -> None:
    example_data["schema_version"] = "2.0"

    with pytest.raises(AiQaError) as excinfo:
        load_settings(write_config(example_data))

    assert excinfo.value.code is ErrorCode.CONFIG_INVALID
    assert any("schema_version" in issue["loc"] for issue in excinfo.value.details["issues"])


def test_relative_target_path_rejected(example_data: dict[str, Any], write_config: Any) -> None:
    example_data["target"]["executable_path"] = "web_server"

    with pytest.raises(AiQaError) as excinfo:
        load_settings(write_config(example_data))

    issues = excinfo.value.details["issues"]
    assert any(issue["loc"] == "target.executable_path" for issue in issues)
    assert any("绝对路径" in issue["msg"] for issue in issues)


def test_invalid_port_rejected(example_data: dict[str, Any], write_config: Any) -> None:
    example_data["target"]["port"] = 70000

    with pytest.raises(AiQaError) as excinfo:
        load_settings(write_config(example_data))

    assert any(issue["loc"] == "target.port" for issue in excinfo.value.details["issues"])


def test_env_var_selects_config(monkeypatch: pytest.MonkeyPatch, example_config_path: Path) -> None:
    monkeypatch.setenv("AI_QA_CONFIG", str(example_config_path))
    assert resolve_config_path() == example_config_path


def test_explicit_config_beats_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AI_QA_CONFIG", "/from/env.yaml")
    explicit = tmp_path / "explicit.yaml"
    assert resolve_config_path(explicit) == explicit


def test_default_config_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_QA_CONFIG", raising=False)
    assert str(resolve_config_path()) == "config/settings.yaml"
