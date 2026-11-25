from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from ai_qa import __version__
from ai_qa.cli import app

runner = CliRunner()


def _combined(result: object) -> str:
    output = getattr(result, "output", "")
    stderr = getattr(result, "stderr", "") or ""
    return output + stderr


def test_help_lists_doctor() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "doctor" in result.output


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == __version__


def test_doctor_json_on_example_config(example_config_path: Path) -> None:
    result = runner.invoke(app, ["doctor", "--config", str(example_config_path), "--json"])

    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["checks"]
    assert any(check["status"] == "ERROR" for check in payload["checks"])
    assert any(check["check"] == "target.executable_path" for check in payload["checks"])


def test_doctor_passes_with_valid_config(valid_config: Path) -> None:
    result = runner.invoke(app, ["doctor", "--config", str(valid_config)])

    assert result.exit_code == 0, _combined(result)
    assert "结果: 通过" in result.output


def test_doctor_missing_config_exits_2(tmp_path: Path) -> None:
    result = runner.invoke(app, ["doctor", "--config", str(tmp_path / "missing.yaml")])

    assert result.exit_code == 2
    assert "CONFIG_INVALID" in _combined(result)


def test_doctor_help_lists_options() -> None:
    result = runner.invoke(app, ["doctor", "--help"])

    assert result.exit_code == 0
    assert "--config" in result.output
    assert "--json" in result.output
