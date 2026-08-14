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


def _merge_texts(t1: str, t2: str) -> str:
    """Merge two chunk texts, removing any overlapping suffix/prefix if present."""
    if not t1:
        return t2
    if not t2:
        return t1
    # Check for exact overlap between end of t1 and start of t2.
    max_overlap = min(len(t1), len(t2))
    for length in range(max_overlap, 9, -1):
        if t1.endswith(t2[:length]):
            return t1 + t2[length:]
    if t1.endswith("\n\n") or t2.startswith("\n\n"):
        return f"{t1.rstrip()}\n\n{t2.lstrip()}"
    return f"{t1}\n\n{t2}"


def merge_adjacent_chunks(chunks: list[Chunk]) -> list[Chunk]:
    """Merge contiguous chunks from the same document (slug).

    When multiple chunks from the same document are retrieved, contiguous
    chunks (e.g. index i and i+1) are merged into unified Chunk objects so
    the prompt receives rich, cohesive document context without duplicate headers.
    """
    if len(chunks) <= 1:
        return list(chunks)

    indexed_chunks: list[tuple[int, Chunk]] = list(enumerate(chunks))
    slug_groups: dict[str, list[tuple[int, Chunk]]] = {}
    passthrough: list[tuple[int, Chunk]] = []

    for pos, c in indexed_chunks:
        if c.slug and c.chunk_index is not None:
            slug_groups.setdefault(c.slug, []).append((pos, c))
        else:
            passthrough.append((pos, c))

    merged_items: list[tuple[int, Chunk]] = []

    for slug, group in slug_groups.items():
        # Deduplicate same chunk_index by keeping the one with higher score or earliest pos
        by_idx: dict[int, tuple[int, Chunk]] = {}
        for pos, c in group:
            idx = c.chunk_index
            if idx not in by_idx:
                by_idx[idx] = (pos, c)
            else:
                existing_pos, existing_c = by_idx[idx]
                if c.score is not None and (existing_c.score is None or c.score > existing_c.score):
                    by_idx[idx] = (min(pos, existing_pos), c)

        sorted_items = [by_idx[idx] for idx in sorted(by_idx.keys())]

        runs: list[list[tuple[int, Chunk]]] = []
        cur_run: list[tuple[int, Chunk]] = []

        for item in sorted_items:
            pos, c = item
            if not cur_run:
                cur_run.append(item)
            else:
                _, prev_c = cur_run[-1]
                if c.chunk_index == prev_c.chunk_index + 1:
                    cur_run.append(item)
                else:
                    runs.append(cur_run)
                    cur_run = [item]
        if cur_run:
            runs.append(cur_run)

        for run in runs:
            earliest_pos = min(p for p, _ in run)
            if len(run) == 1:
                merged_items.append((earliest_pos, run[0][1]))
            else:
                run_chunks = [c for _, c in run]
                first = run_chunks[0]
                last = run_chunks[-1]
                merged_text = run_chunks[0].text
                for nxt in run_chunks[1:]:
                    merged_text = _merge_texts(merged_text, nxt.text)

                scores = [c.score for c in run_chunks if c.score is not None]
                best_score = max(scores) if scores else None
                merged_id = f"{first.slug}#{first.chunk_index}..{last.chunk_index}"
                merged_chunk = Chunk(
                    id=merged_id,
                    slug=first.slug,
                    url=first.url,
                    title=first.title,
                    text=merged_text,
                    score=best_score,
                    chunk_index=first.chunk_index,
                    total_chunks=first.total_chunks,
                )
                merged_items.append((earliest_pos, merged_chunk))

    all_items = merged_items + passthrough
    all_items.sort(key=lambda x: x[0])
    return [c for _, c in all_items]


def retrieve(
    query: str,
    k: int = 5,
    *,
    expand_window: bool = False,
    window_size: int = 1,
    merge_adjacent: bool = False,
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
    chunks = store.query(vec, k=k)
    if expand_window:
        if hasattr(store, "expand_chunks"):
            chunks = store.expand_chunks(chunks, window_size=window_size)
        chunks = merge_adjacent_chunks(chunks)
    elif merge_adjacent:
        chunks = merge_adjacent_chunks(chunks)
    return chunks
