"""Chroma persistence + content_hash bookkeeping (plan §3.3, §3.7).

Embedded, persistent Chroma. We pass embeddings in explicitly (no Chroma
embedding function) so the Embedder seam stays the single source of truth and
nothing is downloaded implicitly.
"""

from __future__ import annotations

from . import config
from .models import Chunk


class VectorStore:
    def __init__(self, path: str | None = None, name: str | None = None):
        import chromadb

        self.client = chromadb.PersistentClient(path=path or config.chroma_dir())
        self.col = self.client.get_or_create_collection(
            name=name or config.collection_name(),
            metadata={"hnsw:space": "cosine"},
        )

    def stored_hash(self, slug: str) -> str | None:
        got = self.col.get(where={"slug": slug}, limit=1, include=["metadatas"])
        metas = got.get("metadatas") or []
        return metas[0]["content_hash"] if metas else None

    def slugs(self) -> set[str]:
        got = self.col.get(include=["metadatas"])
        return {m["slug"] for m in (got.get("metadatas") or [])}

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
                }
                for c in chunks
            ],
        )

    def query(self, embedding: list[float], k: int = 5) -> list[Chunk]:
        res = self.col.query(
            query_embeddings=[embedding],
            n_results=k,
            include=["documents", "metadatas", "distances"],
        )
        docs = res["documents"][0]
        metas = res["metadatas"][0]
        dists = res["distances"][0]
        return [
            Chunk(
                id="",
                slug=m["slug"],
                url=m["url"],
                title=m["title"],
                text=doc,
                score=1.0 - dist,  # cosine distance -> similarity
            )
            for doc, m, dist in zip(docs, metas, dists)
        ]
