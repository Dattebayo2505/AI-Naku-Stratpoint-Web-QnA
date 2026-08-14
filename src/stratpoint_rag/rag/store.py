"""Chroma persistence + content_hash bookkeeping (plan §3.3, §3.7).

Embedded, persistent Chroma. We pass embeddings in explicitly (no Chroma
embedding function) so the Embedder seam stays the single source of truth and
nothing is downloaded implicitly.
"""

from __future__ import annotations

from . import config
from .models import Chunk

# High-recall HNSW settings. The 1.x defaults (max_neighbors=16, ef_*=100) left
# the *true* nearest neighbour unreachable at small k — a chunk that ranked #0 at
# k=200 was absent entirely at k=10/50, silently dropping the best chunk for many
# queries. max_neighbors (graph connectivity) and ef_construction are BUILD-time,
# so applying this needs a fresh index: delete chroma_db/ then re-ingest --force.
_HNSW_CONFIG = {
    "space": "cosine",
    "max_neighbors": 32,      # M — was 16; the key connectivity fix
    "ef_construction": 200,   # was 100 — better graph quality at build time
    "ef_search": 200,         # was 100 — wider query-time search
}


class VectorStore:
    def __init__(self, path: str | None = None, name: str | None = None):
        import chromadb

        self.client = chromadb.PersistentClient(path=path or config.chroma_dir())
        self.col = self.client.get_or_create_collection(
            name=name or config.collection_name(),
            configuration={"hnsw": _HNSW_CONFIG},
        )

    # Metadata is read with .get(), never []. A row missing a key is a partial
    # or older ingest, and the right response is to treat that page as unknown
    # (so it re-embeds) rather than to raise out of an ingest or a chat turn.
    def stored_hash(self, slug: str) -> str | None:
        got = self.col.get(where={"slug": slug}, limit=1, include=["metadatas"])
        metas = got.get("metadatas") or []
        return metas[0].get("content_hash") if metas and metas[0] else None

    def slugs(self) -> set[str]:
        got = self.col.get(include=["metadatas"])
        return {
            m["slug"] for m in (got.get("metadatas") or []) if m and m.get("slug")
        }

    def delete_slug(self, slug: str) -> None:
        self.col.delete(where={"slug": slug})

    def upsert_page(
        self, chunks: list[Chunk], embeddings: list[list[float]], content_hash: str
    ) -> None:
        if not chunks:
            return
        self.delete_slug(chunks[0].slug)  # replace any prior chunks for this page
        self.col.add(
            ids=[c.id for c in chunks],
            embeddings=embeddings,
            documents=[c.text for c in chunks],
            metadatas=[
                {
                    "slug": c.slug,
                    "url": c.url,
                    "title": c.title,
                    "content_hash": content_hash,
                    "chunk_index": c.chunk_index if c.chunk_index is not None else i,
                    "total_chunks": c.total_chunks if c.total_chunks is not None else len(chunks),
                }
                for i, c in enumerate(chunks)
            ],
        )

    def query(self, embedding: list[float], k: int = 5) -> list[Chunk]:
        res = self.col.query(
            query_embeddings=[embedding],
            n_results=k,
            include=["documents", "metadatas", "distances"],
        )
        docs = res.get("documents", [[]])[0] if res.get("documents") else []
        metas = res.get("metadatas", [[]])[0] if res.get("metadatas") else []
        dists = res.get("distances", [[]])[0] if res.get("distances") else []
        ids = res.get("ids", [[]])[0] if res.get("ids") else []

        chunks: list[Chunk] = []
        for i, (doc, dist) in enumerate(zip(docs, dists)):
            m = metas[i] if i < len(metas) and metas[i] else {}
            chunk_id = ids[i] if i < len(ids) and ids[i] else ""
            c_idx = m.get("chunk_index")
            t_chunks = m.get("total_chunks")
            if not chunk_id and m.get("slug") and c_idx is not None:
                chunk_id = f"{m.get('slug')}#{c_idx}"
            chunks.append(
                Chunk(
                    id=chunk_id,
                    slug=m.get("slug") or "",
                    url=m.get("url") or "",
                    title=m.get("title") or "",
                    text=doc,
                    score=1.0 - dist,  # cosine distance -> similarity
                    chunk_index=int(c_idx) if c_idx is not None else None,
                    total_chunks=int(t_chunks) if t_chunks is not None else None,
                )
            )
        return chunks

    def get_chunks_by_ids(self, ids: list[str]) -> list[Chunk]:
        """Fetch chunks by their explicit IDs (e.g. 'slug#0')."""
        if not ids:
            return []
        got = self.col.get(ids=ids, include=["documents", "metadatas"]) or {}
        res_ids = got.get("ids") or []
        docs = got.get("documents") or []
        metas = got.get("metadatas") or []

        if not res_ids and (docs or metas):
            res_ids = ids[:max(len(docs), len(metas))]

        chunks: list[Chunk] = []
        for i, cid in enumerate(res_ids):
            doc = docs[i] if i < len(docs) else ""
            m = metas[i] if i < len(metas) and metas[i] else {}
            c_idx = m.get("chunk_index")
            t_chunks = m.get("total_chunks")
            chunks.append(
                Chunk(
                    id=cid,
                    slug=m.get("slug") or "",
                    url=m.get("url") or "",
                    title=m.get("title") or "",
                    text=doc,
                    score=None,
                    chunk_index=int(c_idx) if c_idx is not None else None,
                    total_chunks=int(t_chunks) if t_chunks is not None else None,
                )
            )
        return chunks

    def expand_chunks(self, chunks: list[Chunk], window_size: int = 1) -> list[Chunk]:
        """Expand retrieved chunks with adjacent neighbors (i - window_size .. i + window_size).

        Fetches missing neighboring chunks from the store and returns the combined set.
        """
        if not chunks or window_size <= 0:
            return list(chunks)

        existing_ids = set()
        for c in chunks:
            if c.id:
                existing_ids.add(c.id)
            if c.slug and c.chunk_index is not None:
                existing_ids.add(f"{c.slug}#{c.chunk_index}")

        needed_ids: list[str] = []
        for c in chunks:
            if not c.slug or c.chunk_index is None:
                continue
            total = c.total_chunks if c.total_chunks is not None else c.chunk_index + window_size + 1
            start = max(0, c.chunk_index - window_size)
            end = min(total, c.chunk_index + window_size + 1)
            for idx in range(start, end):
                nid = f"{c.slug}#{idx}"
                if nid not in existing_ids and nid not in needed_ids:
                    needed_ids.append(nid)

        if not needed_ids:
            return list(chunks)

        fetched = self.get_chunks_by_ids(needed_ids)
        return list(chunks) + fetched
