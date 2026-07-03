"""Embedder interface + local (bge) implementation (plan §3.2).

The interface is the swap seam: local is the default and only wired provider;
cloud (Google AI Studio / NVIDIA NIM) is a documented route, stubbed until the
group commits to it. Query and ingestion MUST use the same embedder.
"""

from __future__ import annotations

from typing import Protocol

from . import config


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class LocalEmbedder:
    """sentence-transformers, normalized vectors for cosine similarity."""

    def __init__(self, model_name: str | None = None):
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model_name or config.embedding_model())

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(
            list(texts), normalize_embeddings=True, show_progress_bar=False
        ).tolist()


def get_embedder() -> Embedder:
    provider = config.embedding_provider()
    if provider == "local":
        return LocalEmbedder()
    # ponytail: cloud route documented in plan §3.2 but not wired — YAGNI until the
    # group chooses it. Implement CloudEmbedder here behind the same interface.
    raise NotImplementedError(
        f"embedding provider {provider!r} not wired yet; 'local' is the default. See plan §3.2."
    )
