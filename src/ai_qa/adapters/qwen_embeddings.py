"""Qwen3-Embedding-0.6B 适配器（实现规格 §2.2 / §10）。"""

from __future__ import annotations

from typing import Any

from ai_qa.domain.knowledge import EmbeddingMetadata

QUERY_INSTRUCTION = "Given a web search query, retrieve relevant passages that answer the query"


class QwenEmbeddingProvider:
    def __init__(
        self,
        model_name: str,
        *,
        device: str = "cpu",
        normalize: bool = True,
    ) -> None:
        from sentence_transformers import SentenceTransformer

        self._model: Any = SentenceTransformer(model_name, device=device)
        self._normalize = normalize
        get_dimension = getattr(
            self._model,
            "get_embedding_dimension",
            getattr(self._model, "get_sentence_embedding_dimension", None),
        )
        dim = get_dimension() if get_dimension is not None else None
        if not isinstance(dim, int) or dim <= 0:
            raise RuntimeError(f"无法获取 Embedding 维度: {dim!r}")
        prompts = getattr(self._model, "prompts", {}) or {}
        self._has_query_prompt = "query" in prompts
        revision = self._resolve_revision(model_name)
        self._metadata = EmbeddingMetadata(
            model=model_name,
            revision=str(revision),
            dim=dim,
            normalize=normalize,
            distance="cosine",
        )

    def metadata(self) -> EmbeddingMetadata:
        return self._metadata

    @staticmethod
    def _resolve_revision(model_name: str) -> str:
        try:
            from huggingface_hub import scan_cache_dir
        except Exception:  # 依赖缺失时仍允许记录 unknown
            return "unknown"
        try:
            repos = [repo for repo in scan_cache_dir().repos if repo.repo_id == model_name]
        except Exception:  # 缓存不可读时不阻塞模型加载
            return "unknown"
        revisions = [revision for repo in repos for revision in repo.revisions]
        if not revisions:
            return "unknown"
        latest = max(revisions, key=lambda revision: revision.last_modified)
        return str(latest.commit_hash)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts, prompt_name=None)

    def embed_query(self, text: str) -> list[float]:
        if self._has_query_prompt:
            return self._encode([text], prompt_name="query")[0]
        prefixed = f"Instruct: {QUERY_INSTRUCTION}\nQuery: {text}"
        return self._encode([prefixed], prompt_name=None)[0]

    def _encode(self, texts: list[str], *, prompt_name: str | None) -> list[list[float]]:
        if not texts:
            return []
        kwargs: dict[str, Any] = {
            "batch_size": 8,
            "normalize_embeddings": self._normalize,
            "convert_to_numpy": True,
            "show_progress_bar": False,
        }
        if prompt_name is not None:
            kwargs["prompt_name"] = prompt_name
        vectors = self._model.encode(texts, **kwargs)
        return [[float(value) for value in vector] for vector in vectors]
