from __future__ import annotations

from ai_qa.domain.knowledge import KnowledgeChunk
from ai_qa.knowledge.bm25 import BM25Index, tokenize


def _chunk(
    chunk_id: str,
    text: str,
    *,
    source_type: str = "project_doc",
    doc_id: str = "doc_1",
    chunk_index: int = 0,
) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        doc_id=doc_id,
        project_id="cpp-webserver",
        source_path=f"knowledge/{doc_id}.md",
        source_type=source_type,
        priority="P0",
        commit="commit",
        section="s",
        chunk_index=chunk_index,
        content_hash=f"hash-{chunk_id}",
        text=text,
    )


def test_tokenize_chinese_and_english() -> None:
    tokens = tokenize("健康检查 Content-Length")

    assert "健" in tokens
    assert "健康" in tokens
    assert "content" in tokens
    assert "length" in tokens


def test_ranks_relevant_chunk_first() -> None:
    chunks = [
        _chunk("chunk_a", "健康检查使用 /healthz 路径，返回 status 和 commit。"),
        _chunk("chunk_b", "wrk 用 -t 指定线程，-c 指定连接数。"),
        _chunk("chunk_c", "路径穿越通过 weakly_canonical 后检查 root，失败返回 403。"),
    ]

    hits = BM25Index(chunks).search("健康检查路径", top_k=3)

    assert hits
    assert hits[0].chunk.chunk_id == "chunk_a"


def test_source_type_filter() -> None:
    chunks = [
        _chunk("chunk_a", "健康检查 /healthz", source_type="project_doc"),
        _chunk("chunk_b", "健康检查 /healthz 标准说明", source_type="standard"),
    ]

    hits = BM25Index(chunks).search("健康检查", top_k=5, source_types=["standard"])

    assert [hit.chunk.chunk_id for hit in hits] == ["chunk_b"]


def test_no_match_returns_empty() -> None:
    chunks = [_chunk("chunk_a", "健康检查")]

    assert BM25Index(chunks).search("完全不相关的词汇", top_k=3) == []


def test_results_are_deterministic() -> None:
    chunks = [
        _chunk("chunk_b", "健康检查"),
        _chunk("chunk_a", "健康检查"),
    ]

    first = BM25Index(chunks).search("健康检查", top_k=2)
    second = BM25Index(chunks).search("健康检查", top_k=2)

    assert [hit.chunk.chunk_id for hit in first] == [hit.chunk.chunk_id for hit in second]
