"""Shared RAG types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    """A retrievable slice of a page, carrying its source for citation."""

    id: str
    slug: str
    url: str
    title: str
    text: str
    score: float | None = None  # cosine similarity, set on retrieval
