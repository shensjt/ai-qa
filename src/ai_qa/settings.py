"""配置模型与加载（实现规格 §8 / §8.3）。

Phase 0 只实现：内置默认值 → YAML → Pydantic 校验 → 结构化 CONFIG_INVALID。
环境变量当前仅支持 ``AI_QA_CONFIG`` 指定配置文件；其余覆盖项在后续阶段按需加入。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

from ai_qa.domain.errors import AiQaError, ErrorCode

CONFIG_ENV_VAR = "AI_QA_CONFIG"
DEFAULT_CONFIG_RELATIVE = Path("config/settings.yaml")


def _ensure_absolute(value: Path) -> Path:
    if not value.is_absolute():
        raise ValueError("必须是绝对路径")
    return value


AbsolutePath = Annotated[Path, AfterValidator(_ensure_absolute)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectSettings(_StrictModel):
    project_id: str = Field(min_length=1)
    repo_root: AbsolutePath
    knowledge_root: Path


class TargetSettings(_StrictModel):
    target_id: str = Field(min_length=1)
    kind: Literal["cpp_webserver"]
    executable_path: AbsolutePath
    working_directory: AbsolutePath
    www_root: AbsolutePath
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    threads: int = Field(ge=1, le=256)
    timeout_seconds: int = Field(gt=0)
    cache_bytes: int = Field(ge=0)
    health_path: str = Field(pattern=r"^/")
    startup_timeout_seconds: int = Field(gt=0)
    stop_timeout_seconds: int = Field(gt=0)


class RuntimeSettings(_StrictModel):
    run_timeout_seconds: int = Field(gt=0)
    case_timeout_seconds: int = Field(gt=0)
    max_cases: int = Field(ge=1, le=100)
    max_log_chars_for_llm: int = Field(gt=0)
    allowed_target_hosts: list[str] = Field(min_length=1)
    allowed_processes: list[AbsolutePath] = Field(default_factory=list)


class KnowledgeSettings(_StrictModel):
    embedding_model: str = Field(min_length=1)
    embedding_device: str = Field(min_length=1)
    embedding_dim: int = Field(gt=0)
    distance: Literal["cosine", "l2", "ip"]
    top_k: int = Field(ge=1, le=100)
    chunk_target_tokens: int = Field(gt=0)
    chunk_overlap_ratio: float = Field(ge=0.0, lt=1.0)


class BenchmarkSettings(_StrictModel):
    enabled_by_default: bool
    max_duration_seconds: int = Field(gt=0)
    max_connections: int = Field(ge=1)
    max_threads: int = Field(ge=1)
    wrk_path: AbsolutePath


class AgentSettings(_StrictModel):
    max_plan_repairs: int = Field(ge=0, le=2)
    llm_timeout_seconds: int = Field(gt=0)
    max_tool_calls: int = Field(ge=1)


class AppSettings(_StrictModel):
    schema_version: Literal["1.0"]
    project: ProjectSettings
    target: TargetSettings
    runtime: RuntimeSettings
    knowledge: KnowledgeSettings
    benchmark: BenchmarkSettings
    agent: AgentSettings


@dataclass(frozen=True)
class LoadedSettings:
    settings: AppSettings
    config_path: Path


def load_local_env(path: Path | None = None) -> None:
    """本地开发便利：加载 .env 到环境变量，但不覆盖已存在的变量。"""
    from dotenv import load_dotenv

    candidate = path or Path(".env")
    if candidate.is_file():
        load_dotenv(candidate, override=False)
    # huggingface_hub 不解析相对 HF_HOME；统一按仓库根目录转成绝对路径。
    for key in ("HF_HOME", "HF_HUB_CACHE", "TRANSFORMERS_CACHE"):
        raw = os.environ.get(key)
        if raw and not Path(raw).expanduser().is_absolute():
            os.environ[key] = str((Path.cwd() / raw).expanduser().resolve())


def resolve_config_path(explicit: Path | None = None) -> Path:
    """CLI 显式指定 > AI_QA_CONFIG > config/settings.yaml。"""
    if explicit is not None:
        return explicit
    from_env = os.environ.get(CONFIG_ENV_VAR, "").strip()
    if from_env:
        return Path(from_env)
    return DEFAULT_CONFIG_RELATIVE


def _validation_issues(exc: ValidationError) -> list[dict[str, str]]:
    return [
        {
            "loc": ".".join(str(part) for part in error["loc"]),
            "msg": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors(include_url=False)[:20]
    ]


def load_settings(config_path: Path) -> LoadedSettings:
    """加载并校验配置；所有失败都映射为 CONFIG_INVALID。"""
    if not config_path.is_file():
        raise AiQaError(
            ErrorCode.CONFIG_INVALID,
            f"配置文件不存在: {config_path}",
            details={"path": str(config_path)},
        )

    try:
        raw: Any = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AiQaError(
            ErrorCode.CONFIG_INVALID,
            f"无法读取配置文件: {config_path}",
            details={"path": str(config_path)},
        ) from exc
    except yaml.YAMLError as exc:
        raise AiQaError(
            ErrorCode.CONFIG_INVALID,
            "配置文件不是合法 YAML",
            details={"path": str(config_path), "error": str(exc)[:500]},
        ) from exc

    if not isinstance(raw, dict):
        raise AiQaError(
            ErrorCode.CONFIG_INVALID,
            "配置文件顶层必须是映射",
            details={"path": str(config_path)},
        )

    try:
        settings = AppSettings.model_validate(raw)
    except ValidationError as exc:
        issues = _validation_issues(exc)
        raise AiQaError(
            ErrorCode.CONFIG_INVALID,
            f"配置校验失败（{len(issues)} 项）",
            details={"path": str(config_path), "issues": issues},
        ) from exc

    return LoadedSettings(settings=settings, config_path=config_path)
