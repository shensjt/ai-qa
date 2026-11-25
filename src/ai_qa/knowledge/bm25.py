"""纯 Python BM25 降级检索（实现规格 §10.5）。

不伪装成向量检索；中文使用单字 + 相邻双字，英文/数字按词切分。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from ai_qa.domain.knowledge import KnowledgeChunk

_WORD = re.compile(r"[A-Za-z0-9_]+")
_CJK_START = "一"
_CJK_END = "鿿"


def tokenize(text: str) -> list[str]:
    tokens = [match.lower() for match in _WORD.findall(text)]
    cjk = [char for char in text if _CJK_START <= char <= _CJK_END]
    tokens.extend(cjk)
    tokens.extend(cjk[index] + cjk[index + 1] for index in range(len(cjk) - 1))
    return tokens


@dataclass(frozen=True)
class ScoredChunk:
    chunk: KnowledgeChunk
    score: float


class BM25Index:
    def __init__(self, chunks: list[KnowledgeChunk], *, k1: float = 1.5, b: float = 0.75) -> None:
        self._chunks = chunks
        self._k1 = k1
        self._b = b
        self._tokens = [tokenize(chunk.text) for chunk in chunks]
        self._lengths = [len(tokens) for tokens in self._tokens]
        self._avg_length = sum(self._lengths) / len(self._lengths) if self._lengths else 0.0
        self._term_freqs = [Counter(tokens) for tokens in self._tokens]
        document_freq: Counter[str] = Counter()
        for tokens in self._tokens:
            document_freq.update(set(tokens))
        total = len(chunks)
        self._idf = {
            term: math.log(1.0 + (total - freq + 0.5) / (freq + 0.5))
            for term, freq in document_freq.items()
        }

    def search(
        self,
        query: str,
        *,
        top_k: int = 8,
        source_types: list[str] | None = None,
    ) -> list[ScoredChunk]:
        query_tokens = tokenize(query)
        if not query_tokens or not self._chunks:
            return []

        scored: list[ScoredChunk] = []
        for index, chunk in enumerate(self._chunks):
            if source_types is not None and chunk.source_type not in source_types:
                continue
            length = self._lengths[index]
            if length == 0:
                continue
            freq = self._term_freqs[index]
            score = 0.0
            for token in query_tokens:
                if token not in freq:
                    continue
                tf = freq[token]
                denominator = tf + self._k1 * (1.0 - self._b + self._b * length / self._avg_length)
                score += self._idf.get(token, 0.0) * tf * (self._k1 + 1.0) / denominator
            if score > 0:
                scored.append(ScoredChunk(chunk=chunk, score=score))

        scored.sort(key=lambda item: (-item.score, item.chunk.chunk_id))
        return scored[:top_k]
