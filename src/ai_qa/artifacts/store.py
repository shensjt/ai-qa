"""文件型 Artifact Store（实现规格 §14.3）。

安全约束：run_id/name 不能逃逸 root；写入使用临时文件 + rename，避免半文件。
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import tempfile
from pathlib import Path, PurePosixPath

from ai_qa.domain.errors import AiQaError, ErrorCode
from ai_qa.domain.models import new_id, utc_now
from ai_qa.domain.results import ArtifactRef

_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _validate_run_id(run_id: str) -> None:
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise AiQaError(
            ErrorCode.ARTIFACT_WRITE_FAILED,
            f"run_id 非法: {run_id!r}",
            details={"run_id": run_id},
        )


def _validate_name(name: str) -> None:
    if not name or name.startswith(("/", "\\")) or "\\" in name:
        raise AiQaError(
            ErrorCode.ARTIFACT_WRITE_FAILED,
            f"Artifact 名称必须是相对路径: {name!r}",
            details={"name": name},
        )
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in name):
        raise AiQaError(
            ErrorCode.ARTIFACT_WRITE_FAILED,
            "Artifact 名称包含控制字符",
            details={"name": name},
        )
    parts = PurePosixPath(name).parts
    if not parts or any(part in {"..", "."} for part in parts):
        raise AiQaError(
            ErrorCode.ARTIFACT_WRITE_FAILED,
            f"Artifact 名称不允许包含 . 或 .. 路径段: {name!r}",
            details={"name": name},
        )


class FileArtifactStore:
    def __init__(self, root: Path) -> None:
        self._root = root.expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def write_text(
        self,
        run_id: str,
        name: str,
        text: str,
        kind: str,
        *,
        mime: str | None = None,
    ) -> ArtifactRef:
        return self.write_bytes(
            run_id,
            name,
            text.encode("utf-8"),
            kind,
            mime=mime or "text/plain; charset=utf-8",
        )

    def write_bytes(
        self,
        run_id: str,
        name: str,
        data: bytes,
        kind: str,
        *,
        mime: str | None = None,
    ) -> ArtifactRef:
        target = self._resolve_target(run_id, name)
        resolved_mime = mime or mimetypes.guess_type(name)[0] or "application/octet-stream"

        temp_path: str | None = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_path = tempfile.mkstemp(
                dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
            )
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target)
            temp_path = None
        except OSError as exc:
            raise AiQaError(
                ErrorCode.ARTIFACT_WRITE_FAILED,
                f"Artifact 写入失败: {name}",
                details={"name": name, "run_id": run_id},
            ) from exc
        finally:
            if temp_path is not None:
                Path(temp_path).unlink(missing_ok=True)

        return ArtifactRef(
            artifact_id=new_id("art"),
            run_id=run_id,
            name=name,
            kind=kind,
            uri=target.relative_to(self._root).as_posix(),
            mime=resolved_mime,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            created_at=utc_now(),
        )

    def _resolve_target(self, run_id: str, name: str) -> Path:
        _validate_run_id(run_id)
        _validate_name(name)

        run_root = (self._root / run_id).resolve()
        target = (run_root / name).resolve()
        if target == run_root or not target.is_relative_to(run_root):
            raise AiQaError(
                ErrorCode.ARTIFACT_WRITE_FAILED,
                f"Artifact 路径逃逸: {name!r}",
                details={"name": name, "run_id": run_id},
            )
        return target
