from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ai_qa.doctor import CheckStatus, DoctorReport, DoctorService
from ai_qa.settings import load_settings


def _report(config_path: Path) -> DoctorReport:
    loaded = load_settings(config_path)
    return DoctorService(loaded.settings, loaded.config_path).run()


def _status_of(report: DoctorReport, check: str) -> CheckStatus:
    for result in report.checks:
        if result.check == check:
            return result.status
    raise AssertionError(f"doctor 未包含检查项 {check}")


def test_valid_config_has_no_errors(valid_config: Path) -> None:
    report = _report(valid_config)

    assert report.has_errors is False, [
        check.model_dump() for check in report.checks if check.status is CheckStatus.ERROR
    ]
    assert _status_of(report, "target.executable_path") is CheckStatus.OK
    assert _status_of(report, "target.www_root") is CheckStatus.OK


def test_example_config_reports_missing_target_paths(example_config_path: Path) -> None:
    report = _report(example_config_path)

    assert report.has_errors is True
    assert _status_of(report, "target.executable_path") is CheckStatus.ERROR
    assert _status_of(report, "project.repo_root") is CheckStatus.ERROR


def test_host_outside_allowlist_is_error(example_data: dict[str, Any], write_config: Any) -> None:
    example_data["target"]["host"] = "0.0.0.0"

    report = _report(write_config(example_data))

    assert _status_of(report, "target.host") is CheckStatus.ERROR


def test_missing_wrk_is_warning(valid_config: Path, tmp_path: Path) -> None:
    data = yaml.safe_load(valid_config.read_text(encoding="utf-8"))
    data["benchmark"]["wrk_path"] = str(tmp_path / "no-wrk")
    valid_config.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    report = _report(valid_config)

    assert _status_of(report, "benchmark.wrk_path") is CheckStatus.WARN
    assert report.has_errors is False


def test_allowed_process_missing_is_error(
    example_data: dict[str, Any], write_config: Any, tmp_path: Path
) -> None:
    example_data["runtime"]["allowed_processes"] = [str(tmp_path / "not-there")]

    report = _report(write_config(example_data))

    assert _status_of(report, "runtime.allowed_processes") is CheckStatus.ERROR
