from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = REPO_ROOT / "config" / "settings.example.yaml"


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def example_config_path() -> Path:
    return EXAMPLE_CONFIG


@pytest.fixture
def example_data() -> dict[str, Any]:
    loaded = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


@pytest.fixture
def write_config(tmp_path: Path) -> Any:
    def _write(data: dict[str, Any], name: str = "settings.yaml") -> Path:
        path = tmp_path / name
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        return path

    return _write


@pytest.fixture
def valid_config(tmp_path: Path, example_data: dict[str, Any], write_config: Any) -> Path:
    """生成一份所有路径都真实存在的配置，供 doctor/CLI 测试使用。"""
    root = tmp_path / "cpp"
    www = root / "www"
    www.mkdir(parents=True)

    executable = root / "web_server"
    _write_script(executable)

    wrk = tmp_path / "bin" / "wrk"
    wrk.parent.mkdir()
    _write_script(wrk)

    example_data["project"]["repo_root"] = str(root)
    example_data["target"]["executable_path"] = str(executable)
    example_data["target"]["working_directory"] = str(root)
    example_data["target"]["www_root"] = str(www)
    example_data["runtime"]["allowed_processes"] = [str(executable), str(wrk)]
    example_data["benchmark"]["wrk_path"] = str(wrk)
    return write_config(example_data)


def _write_script(path: Path) -> None:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
