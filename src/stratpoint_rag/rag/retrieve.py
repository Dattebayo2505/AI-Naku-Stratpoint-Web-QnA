"""Public seam: retrieve(query, k) -> chunks + sources (plan §3.5).

This is the integration point the ReAct agent (a teammate's module) calls as a
tool. The embedder/store are lazily built once per process; tests inject fakes.
"""

from __future__ import annotations

from .embeddings import Embedder, get_embedder
from .models import Chunk
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
    vec = embedder.embed([query])[0]
    return store.query(vec, k=k)
