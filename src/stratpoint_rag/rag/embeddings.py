"""Embedder interface + local (bge) implementation (plan §3.2).

The interface is the swap seam: local is the default and only wired provider;
cloud (Google AI Studio / NVIDIA NIM) is a documented route, stubbed until the
group commits to it. Query and ingestion MUST use the same embedder.
"""

from __future__ import annotations

import threading
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


_cached_embedder: Embedder | None = None
# Guards the cold build so the startup warmup thread and the first request can't
# each load the (30-90s) model — double-checked below to keep the hot path lock-free.
_embedder_lock = threading.Lock()


def get_embedder() -> Embedder:
    # Process-wide singleton: loading the SentenceTransformer model is expensive
    # (~1-3s) and callers on the hot path (retrieve, HallucinationChecker) hit
    # this per request. The model is stateless, so one shared instance is safe.
    global _cached_embedder
    if _cached_embedder is not None:
        return _cached_embedder

    with _embedder_lock:
        if _cached_embedder is not None:
            return _cached_embedder

        provider = config.embedding_provider()
        if provider == "local":
            import time

            from stratpoint_rag._timing import note

            t0 = time.perf_counter()
            _cached_embedder = LocalEmbedder()
            note(f"loaded embedder model (cold, once per process) in {(time.perf_counter() - t0) * 1000:.0f} ms")
            return _cached_embedder
        # ponytail: cloud route documented in plan §3.2 but not wired — YAGNI until the
        # group chooses it. Implement CloudEmbedder here behind the same interface.
        raise NotImplementedError(
            f"embedding provider {provider!r} not wired yet; 'local' is the default. See plan §3.2."
        )
