from __future__ import annotations

from itertools import pairwise

from ai_qa.knowledge.chunking import chunk_markdown, estimate_tokens


def test_section_paths_from_headings() -> None:
    text = "# A\n\nintro\n\n## B\n\nbody b\n\n# C\n\nbody c\n"

    chunks = chunk_markdown(text, target_tokens=100)

    assert [chunk.section for chunk in chunks] == ["A", "A > B", "C"]


def test_code_fence_is_not_split() -> None:
    text = "# A\n\n```\nline1\nline2\nline3\n```\n\nafter\n"

    chunks = chunk_markdown(text, target_tokens=5, overlap_ratio=0.0)

    assert chunks
    for chunk in chunks:
        assert chunk.text.count("```") % 2 == 0


def test_table_is_kept_as_block() -> None:
    text = "# A\n\n| a | b |\n| - | - |\n| 1 | 2 |\n"

    chunks = chunk_markdown(text, target_tokens=5, overlap_ratio=0.0)

    assert len(chunks) == 1
    assert "| 1 | 2 |" in chunks[0].text


def test_overlap_keeps_trailing_block() -> None:
    paragraphs = [f"paragraph {index} " + "x" * 30 for index in range(6)]
    text = "# A\n\n" + "\n\n".join(paragraphs) + "\n"

    chunks = chunk_markdown(text, target_tokens=40, overlap_ratio=0.3)

    assert len(chunks) > 1
    overlap_found = False
    for first, second in pairwise(chunks):
        first_blocks = set(first.text.split("\n\n")[1:])
        second_blocks = set(second.text.split("\n\n")[1:])
        if first_blocks & second_blocks:
            overlap_found = True
    assert overlap_found


def test_chunking_is_deterministic() -> None:
    text = "# A\n\n" + "\n\n".join(f"line {index}" for index in range(20))

    first = chunk_markdown(text, target_tokens=30, overlap_ratio=0.1)
    second = chunk_markdown(text, target_tokens=30, overlap_ratio=0.1)

    assert first == second
    assert [chunk.chunk_index for chunk in first] == list(range(len(first)))


def test_estimate_tokens_counts_cjk() -> None:
    assert estimate_tokens("健康检查") == 4
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("") >= 1


def test_invalid_parameters_rejected() -> None:
    import pytest

    with pytest.raises(ValueError):
        chunk_markdown("x", target_tokens=0)
    with pytest.raises(ValueError):
        chunk_markdown("x", target_tokens=10, overlap_ratio=1.0)
