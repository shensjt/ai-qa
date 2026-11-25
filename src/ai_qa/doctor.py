"""环境与配置诊断（Phase 0）。

只读检查：不启动被测服务、不写文件、不读取密钥值。
"""

from __future__ import annotations

import os
import shutil
import sys
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ai_qa.settings import AppSettings

DEFAULT_ARTIFACT_ROOT = "artifacts"
DEFAULT_SQLITE_PATH = ".data/ai_qa.db"
DEFAULT_CHROMA_PATH = ".data/chroma"


class CheckStatus(StrEnum):
    OK = "OK"
    WARN = "WARN"
    ERROR = "ERROR"


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check: str
    status: CheckStatus
    message: str


class DoctorReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checks: list[CheckResult] = Field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for check in self.checks if check.status is CheckStatus.ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for check in self.checks if check.status is CheckStatus.WARN)

    @property
    def has_errors(self) -> bool:
        return self.error_count > 0


def _ok(check: str, message: str) -> CheckResult:
    return CheckResult(check=check, status=CheckStatus.OK, message=message)


def _warn(check: str, message: str) -> CheckResult:
    return CheckResult(check=check, status=CheckStatus.WARN, message=message)


def _err(check: str, message: str) -> CheckResult:
    return CheckResult(check=check, status=CheckStatus.ERROR, message=message)


def _nearest_existing(path: Path) -> Path | None:
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return None


class DoctorService:
    def __init__(self, settings: AppSettings, config_path: Path) -> None:
        self._settings = settings
        self._config_path = config_path

    def run(self) -> DoctorReport:
        settings = self._settings
        checks = [
            self._check_python(),
            self._check_config_file(),
            self._check_directory("project.repo_root", settings.project.repo_root),
            self._check_directory("target.working_directory", settings.target.working_directory),
            self._check_executable("target.executable_path", settings.target.executable_path),
            self._check_directory("target.www_root", settings.target.www_root),
            self._check_target_host(),
            self._check_allowed_processes(),
            self._check_benchmark_tool(),
            self._check_writable_location(
                "artifact_root",
                Path(os.environ.get("ARTIFACT_ROOT", DEFAULT_ARTIFACT_ROOT)),
            ),
            self._check_writable_location(
                "sqlite_path",
                Path(os.environ.get("SQLITE_PATH", DEFAULT_SQLITE_PATH)).parent,
            ),
            self._check_writable_location(
                "chroma_path",
                Path(os.environ.get("CHROMA_PATH", DEFAULT_CHROMA_PATH)),
            ),
            self._check_deepseek_key(),
            self._check_git(),
        ]
        return DoctorReport(checks=checks)

    def _check_python(self) -> CheckResult:
        current = ".".join(str(part) for part in sys.version_info[:3])
        # 运行期防御：doctor 可能被用于诊断绕过 requires-python 解释器直接运行源码的环境。
        if sys.version_info[:2] < (3, 11):  # noqa: UP036
            return _err("python", f"需要 Python 3.11+，当前 {current}")
        return _ok("python", f"Python {current}")

    def _check_config_file(self) -> CheckResult:
        if not self._config_path.is_file():
            return _err("config", f"配置文件不存在: {self._config_path}")
        return _ok(
            "config",
            f"已加载 {self._config_path}（schema {self._settings.schema_version}）",
        )

    def _check_directory(self, check: str, path: Path) -> CheckResult:
        if not path.exists():
            return _err(check, f"目录不存在: {path}；请在配置中填写真实路径")
        if not path.is_dir():
            return _err(check, f"不是目录: {path}")
        return _ok(check, f"目录存在: {path}")

    def _check_executable(self, check: str, path: Path) -> CheckResult:
        if not path.exists():
            return _err(check, f"文件不存在: {path}；请在配置中填写真实路径")
        if not path.is_file():
            return _err(check, f"不是普通文件: {path}")
        if not os.access(path, os.X_OK):
            return _err(check, f"文件不可执行: {path}")
        return _ok(check, f"可执行: {path}")

    def _check_target_host(self) -> CheckResult:
        host = self._settings.target.host
        allowed = self._settings.runtime.allowed_target_hosts
        if host not in allowed:
            return _err(
                "target.host",
                f"host {host!r} 不在 runtime.allowed_target_hosts {allowed!r} 中",
            )
        return _ok("target.host", f"{host} 在白名单内")

    def _check_allowed_processes(self) -> CheckResult:
        processes = self._settings.runtime.allowed_processes
        if not processes:
            return _warn(
                "runtime.allowed_processes",
                "白名单为空；后续执行阶段将无法启动任何外部进程",
            )
        missing = [
            str(path) for path in processes if not (path.is_file() and os.access(path, os.X_OK))
        ]
        if missing:
            return _err(
                "runtime.allowed_processes",
                f"以下白名单进程不存在或不可执行: {', '.join(missing)}",
            )
        return _ok("runtime.allowed_processes", f"{len(processes)} 个进程可执行")

    def _check_benchmark_tool(self) -> CheckResult:
        wrk_path = self._settings.benchmark.wrk_path
        if wrk_path.is_file() and os.access(wrk_path, os.X_OK):
            return _ok("benchmark.wrk_path", f"可执行: {wrk_path}")
        return _warn(
            "benchmark.wrk_path",
            f"未找到可执行的 wrk: {wrk_path}；benchmark 默认关闭，Phase 6 需要",
        )

    def _check_writable_location(self, check: str, directory: Path) -> CheckResult:
        ancestor = _nearest_existing(directory)
        if ancestor is None:
            return _err(check, f"无法找到可写目录: {directory}")
        if not ancestor.is_dir():
            return _err(check, f"最近的已存在路径不是目录: {ancestor}")
        if not os.access(ancestor, os.W_OK | os.X_OK):
            return _err(check, f"目录不可写: {ancestor}")
        return _ok(check, f"可写: {directory}")

    def _check_deepseek_key(self) -> CheckResult:
        if os.environ.get("DEEPSEEK_API_KEY", "").strip():
            return _ok("deepseek_api_key", "已设置（不读取、不显示其值）")
        return _warn(
            "deepseek_api_key",
            "未设置；固定计划模式不需要，Agent 模式需要",
        )

    def _check_git(self) -> CheckResult:
        git_path = shutil.which("git")
        if git_path:
            return _ok("git", f"可用: {git_path}")
        return _warn("git", "未找到 git；运行元数据中的 commit 将不可用")
