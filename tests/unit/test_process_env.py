from __future__ import annotations

from pathlib import Path

from ai_qa.bootstrap import resolve_runtime_paths
from ai_qa.execution.process import minimal_child_env


def test_minimal_child_env_excludes_secrets(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-secret")
    monkeypatch.setenv("PATH", "/usr/bin")

    env = minimal_child_env()

    assert "DEEPSEEK_API_KEY" not in env
    assert env["PATH"] == "/usr/bin"
    assert minimal_child_env({"EXTRA": "1"})["EXTRA"] == "1"


def test_server_env_excludes_secrets(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-secret")
    paths = resolve_runtime_paths(
        Path(__file__).resolve().parents[2] / "config" / "settings.example.yaml"
    )

    env = paths.server_env()

    assert "DEEPSEEK_API_KEY" not in env
    assert "AI_QA_CONFIG" in env
    assert "SQLITE_PATH" in env
