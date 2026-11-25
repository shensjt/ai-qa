"""Markdown 感知的结构化切块（实现规格 §10.3）。

- 保留标题路径；
- 代码块与表格不机械从中间截断；
- chunk 目标约 600 tokens，重叠约 10%；
- chunk_id / content_hash 稳定。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE = re.compile(r"^\s*```")
_CJK_RANGE = ("一", "鿿")


@dataclass(frozen=True)
class RawChunk:
    section: str
    chunk_index: int
    text: str
    content_hash: str


def estimate_tokens(text: str) -> int:
    """粗略 token 估算：CJK 按 1 字 1 token，其他按 4 字符 1 token。"""
    cjk = sum(1 for char in text if _CJK_RANGE[0] <= char <= _CJK_RANGE[1])
    return max(1, cjk + (len(text) - cjk) // 4)


def _blocks_with_sections(text: str) -> list[tuple[str, list[str]]]:
    sections: list[tuple[str, list[str]]] = []
    stack: list[tuple[int, str]] = []
    blocks: list[str] = []
    paragraph: list[str] = []
    code: list[str] = []
    in_code = False
    table: list[str] = []

    def section_name() -> str:
        return " > ".join(title for _level, title in stack) or "(root)"

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append("\n".join(paragraph).strip())
            paragraph.clear()

    def flush_table() -> None:
        if table:
            blocks.append("\n".join(table).strip())
            table.clear()

    def flush_section() -> None:
        flush_paragraph()
        flush_table()
        if blocks:
            sections.append((section_name(), list(blocks)))
            blocks.clear()

    lines = text.splitlines()
    for line in lines:
        if in_code:
            code.append(line)
            if _FENCE.match(line):
                blocks.append("\n".join(code).strip())
                code.clear()
                in_code = False
            continue

        heading = _HEADING.match(line)
        if heading:
            flush_section()
            level = len(heading.group(1))
            title = heading.group(2).strip()
            stack = [(lvl, name) for lvl, name in stack if lvl < level]
            stack.append((level, title))
            continue

        if _FENCE.match(line):
            flush_paragraph()
            flush_table()
            in_code = True
            code.append(line)
            continue

        if line.strip().startswith("|"):
            flush_paragraph()
            table.append(line)
            continue

        if not line.strip():
            flush_paragraph()
            flush_table()
            continue

        flush_table()
        paragraph.append(line)

    if in_code and code:
        blocks.append("\n".join(code).strip())
    flush_section()
    return sections


def chunk_markdown(
    text: str,
    *,
    target_tokens: int = 600,
    overlap_ratio: float = 0.1,
) -> list[RawChunk]:
    if target_tokens <= 0:
        raise ValueError("target_tokens 必须为正数")
    if not 0.0 <= overlap_ratio < 1.0:
        raise ValueError("overlap_ratio 必须在 [0, 1) 内")

    overlap_tokens = max(1, int(target_tokens * overlap_ratio))
    chunks: list[RawChunk] = []
    chunk_index = 0

    for section, blocks in _blocks_with_sections(text):
        current: list[str] = []
        current_tokens = 0

        def emit(body: list[str], section: str = section) -> None:
            nonlocal chunk_index
            if not body:
                return
            content = f"{section}\n\n" + "\n\n".join(body)
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            chunks.append(
                RawChunk(
                    section=section,
                    chunk_index=chunk_index,
                    text=content,
                    content_hash=digest,
                )
            )
            chunk_index += 1

        for block in blocks:
            block_tokens = estimate_tokens(block)
            if current and current_tokens + block_tokens > target_tokens:
                emit(current)
                carry: list[str] = []
                carry_tokens = 0
                for previous in reversed(current):
                    previous_tokens = estimate_tokens(previous)
                    if carry_tokens + previous_tokens > overlap_tokens:
                        break
                    carry.insert(0, previous)
                    carry_tokens += previous_tokens
                current = carry
                current_tokens = carry_tokens
            current.append(block)
            current_tokens += block_tokens

        emit(current)

    return chunks
