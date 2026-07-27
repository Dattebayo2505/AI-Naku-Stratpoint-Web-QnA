"""Public seam: retrieve(query, k) -> chunks + sources (plan §3.5).

This is the integration point the ReAct agent (a teammate's module) calls as a
tool. The embedder/store are lazily built once per process; tests inject fakes.
"""

from __future__ import annotations

from .embeddings import Embedder, get_embedder
from .models import Chunk
from .query_rewrite import anchor_entity
from .store import VectorStore

_embedder: Embedder | None = None
_store: VectorStore | None = None


def retrieve(
    query: str,
    k: int = 5,
    *,
    embedder: Embedder | None = None,
    store: VectorStore | None = None,
) -> list[Chunk]:
    global _embedder, _store
    if embedder is None:
        _embedder = _embedder or get_embedder()
        embedder = _embedder
    if store is None:
        _store = _store or VectorStore()
        store = _store
    # Anchored here rather than at a caller so every retrieval path — the direct
    # answer_grounded() route and both agent tools — gets it from one place.
    # Only the embedded text changes; the query shown to the LLM is untouched.
    vec = embedder.embed([anchor_entity(query)])[0]
    return store.query(vec, k=k)
