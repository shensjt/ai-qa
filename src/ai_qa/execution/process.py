"""外部进程环境与安全辅助（实现规格 §17.1）。"""

from __future__ import annotations

import os
from collections.abc import Mapping

# 只传外部进程真正需要的变量，避免把 DEEPSEEK_API_KEY 等密钥泄漏给被测进程或 wrk。
_ALLOWED_ENV_KEYS = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TZ",
    "TMPDIR",
    "TEMP",
    "TMP",
    "LD_LIBRARY_PATH",
)


def minimal_child_env(
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    env = {key: value for key in _ALLOWED_ENV_KEYS if (value := os.environ.get(key)) is not None}
    if extra:
        env.update(extra)
    return env
