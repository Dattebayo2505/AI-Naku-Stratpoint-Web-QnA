"""Unit tests for Issue B: Context Windowing & Adjacent Chunk Retrieval.

Tests:
1. chunk_page stamps chunk_index and total_chunks.
2. upsert_page and query preserve chunk_index and total_chunks in Chroma metadata.
3. get_chunks_by_ids and expand_chunks retrieve adjacent chunks from VectorStore.
4. merge_adjacent_chunks merges contiguous chunks from the same document.
5. retrieve() integrates expand_window and merge_adjacent while preserving backwards compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass
import pytest

from stratpoint_rag.rag.chunker import chunk_page
from stratpoint_rag.rag.models import Chunk
from stratpoint_rag.rag.retrieve import _merge_texts, merge_adjacent_chunks, retrieve
from stratpoint_rag.rag.store import VectorStore


@dataclass
class _DummyPage:
    slug: str
    url: str
    title: str
    body: str


class _FakeCollection:
    """Mock Chroma collection for testing metadata and query/get behavior."""

    def __init__(self, metadatas=None, query_result=None, get_result=None):
        self._metadatas = metadatas or []
        self._query_result = query_result
        self._get_result = get_result
        self.added_records = []
        self.deleted_slugs = []

    def get(self, ids=None, where=None, limit=None, include=None):
        if ids is not None and self._get_result is not None:
            return self._get_result
        return {"metadatas": self._metadatas}

    def query(self, **kwargs):
        return self._query_result

    def add(self, ids, embeddings, documents, metadatas):
        self.added_records.append({
            "ids": ids,
            "embeddings": embeddings,
            "documents": documents,
            "metadatas": metadatas,
        })

    def delete(self, where=None):
        if where and "slug" in where:
            self.deleted_slugs.append(where["slug"])


def _store(collection: _FakeCollection) -> VectorStore:
    store = VectorStore.__new__(VectorStore)
    store.col = collection
    return store


class _FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


# ── 1. Chunker index stamping ──────────────────────────────────────────────


def test_chunk_page_stamps_indices_on_single_chunk():
    page = _DummyPage(
        slug="single-page",
        url="https://stratpoint.com/single",
        title="Single Chunk Page",
        body="Short body that easily fits in one chunk.",
    )
    chunks = chunk_page(page)
    assert len(chunks) == 1
    assert chunks[0].chunk_index == 0
    assert chunks[0].total_chunks == 1
    assert chunks[0].id == "single-page#0"
    assert chunks[0].slug == "single-page"
    assert chunks[0].title == "Single Chunk Page"


def test_chunk_page_stamps_indices_on_multi_chunk():
    paragraphs = [
        f"Paragraph {i}: " + ("text " * 60)
        for i in range(5)
    ]
    page = _DummyPage(
        slug="multi-page",
        url="https://stratpoint.com/multi",
        title="Multi Chunk Page",
        body="\n\n".join(paragraphs),
    )
    chunks = chunk_page(page)
    assert len(chunks) > 1
    total = len(chunks)
    for i, c in enumerate(chunks):
        assert c.chunk_index == i
        assert c.total_chunks == total
        assert c.id == f"multi-page#{i}"


# ── 2. VectorStore metadata persistence and query recovery ─────────────────


def test_upsert_page_saves_chunk_indices_in_metadata():
    col = _FakeCollection()
    store = _store(col)

    chunks = [
        Chunk(
            id="page#0",
            slug="page",
            url="https://stratpoint.com/page",
            title="Page",
            text="chunk 0 text",
            chunk_index=0,
            total_chunks=2,
        ),
        Chunk(
            id="page#1",
            slug="page",
            url="https://stratpoint.com/page",
            title="Page",
            text="chunk 1 text",
            chunk_index=1,
            total_chunks=2,
        ),
    ]
    embeddings = [[0.1, 0.2], [0.3, 0.4]]
    store.upsert_page(chunks, embeddings, content_hash="hash123")

    assert len(col.added_records) == 1
    record = col.added_records[0]
    assert record["ids"] == ["page#0", "page#1"]
    assert record["metadatas"] == [
        {
            "slug": "page",
            "url": "https://stratpoint.com/page",
            "title": "Page",
            "content_hash": "hash123",
            "chunk_index": 0,
            "total_chunks": 2,
        },
        {
            "slug": "page",
            "url": "https://stratpoint.com/page",
            "title": "Page",
            "content_hash": "hash123",
            "chunk_index": 1,
            "total_chunks": 2,
        },
    ]


def test_query_preserves_chunk_index_and_total_chunks():
    query_result = {
        "ids": [["page#1", "other#0"]],
        "documents": [["text one", "text two"]],
        "metadatas": [[
            {
                "slug": "page",
                "url": "https://stratpoint.com/page",
                "title": "Page",
                "chunk_index": 1,
                "total_chunks": 3,
            },
            {
                "slug": "other",
                "url": "https://stratpoint.com/other",
                "title": "Other",
                "chunk_index": 0,
                "total_chunks": 1,
            },
        ]],
        "distances": [[0.15, 0.25]],
    }
    col = _FakeCollection(query_result=query_result)
    store = _store(col)

    chunks = store.query([0.1, 0.2], k=2)
    assert len(chunks) == 2
    assert chunks[0].id == "page#1"
    assert chunks[0].chunk_index == 1
    assert chunks[0].total_chunks == 3
    assert chunks[0].score == pytest.approx(0.85)

    assert chunks[1].id == "other#0"
    assert chunks[1].chunk_index == 0
    assert chunks[1].total_chunks == 1


def test_query_handles_legacy_metadata_missing_indices():
    query_result = {
        "documents": [["body only"]],
        "metadatas": [[{"slug": "legacy", "url": "u", "title": "T"}]],
        "distances": [[0.1]],
    }
    col = _FakeCollection(query_result=query_result)
    store = _store(col)

    chunks = store.query([0.0], k=1)
    assert len(chunks) == 1
    assert chunks[0].chunk_index is None
    assert chunks[0].total_chunks is None
    assert chunks[0].id == ""


# ── 3. VectorStore get_chunks_by_ids and expand_chunks ─────────────────────


def test_get_chunks_by_ids():
    get_result = {
        "ids": ["doc#0", "doc#1"],
        "documents": ["doc 0 content", "doc 1 content"],
        "metadatas": [
            {"slug": "doc", "url": "https://s.com", "title": "Doc", "chunk_index": 0, "total_chunks": 2},
            {"slug": "doc", "url": "https://s.com", "title": "Doc", "chunk_index": 1, "total_chunks": 2},
        ],
    }
    col = _FakeCollection(get_result=get_result)
    store = _store(col)

    chunks = store.get_chunks_by_ids(["doc#0", "doc#1"])
    assert len(chunks) == 2
    assert chunks[0].id == "doc#0"
    assert chunks[0].text == "doc 0 content"
    assert chunks[0].chunk_index == 0
    assert chunks[1].id == "doc#1"
    assert chunks[1].text == "doc 1 content"
    assert chunks[1].chunk_index == 1


def test_expand_chunks_retrieves_missing_adjacent_chunks():
    # Store contains chunks 0, 1, 2 for "article"
    get_result = {
        "ids": ["article#0", "article#2"],
        "documents": ["intro text", "conclusion text"],
        "metadatas": [
            {"slug": "article", "url": "https://s.com/a", "title": "Article", "chunk_index": 0, "total_chunks": 3},
            {"slug": "article", "url": "https://s.com/a", "title": "Article", "chunk_index": 2, "total_chunks": 3},
        ],
    }
    col = _FakeCollection(get_result=get_result)
    store = _store(col)

    # Initial retrieval only returned chunk 1 (body)
    initial_chunks = [
        Chunk(
            id="article#1",
            slug="article",
            url="https://s.com/a",
            title="Article",
            text="body text",
            score=0.9,
            chunk_index=1,
            total_chunks=3,
        )
    ]

    expanded = store.expand_chunks(initial_chunks, window_size=1)
    # Should contain initial chunk 1 + fetched chunk 0 and chunk 2
    assert len(expanded) == 3
    ids = [c.id for c in expanded]
    assert ids == ["article#1", "article#0", "article#2"]


def test_expand_chunks_boundary_clamping():
    # Chunk at index 0 of 2 should only request index 1 (not index -1)
    get_result = {
        "ids": ["first#1"],
        "documents": ["chunk 1"],
        "metadatas": [{"slug": "first", "url": "u", "title": "T", "chunk_index": 1, "total_chunks": 2}],
    }
    col = _FakeCollection(get_result=get_result)
    store = _store(col)

    initial_chunks = [
        Chunk(
            id="first#0",
            slug="first",
            url="u",
            title="T",
            text="chunk 0",
            score=0.95,
            chunk_index=0,
            total_chunks=2,
        )
    ]

    expanded = store.expand_chunks(initial_chunks, window_size=1)
    assert len(expanded) == 2
    assert [c.id for c in expanded] == ["first#0", "first#1"]


# ── 4. Chunk text merging and adjacent chunk consolidation ─────────────────


def test_merge_texts_removes_overlap():
    t1 = "Stratpoint provides modern data platform services. AI-driven systems scale reliably."
    t2 = "AI-driven systems scale reliably. Contact our team today."
    merged = _merge_texts(t1, t2)
    assert merged == "Stratpoint provides modern data platform services. AI-driven systems scale reliably. Contact our team today."


def test_merge_texts_no_overlap_joins_with_double_newline():
    t1 = "First section header and details."
    t2 = "Second section with completely distinct wording."
    merged = _merge_texts(t1, t2)
    assert merged == "First section header and details.\n\nSecond section with completely distinct wording."


def test_merge_adjacent_chunks_contiguous_single_slug():
    chunks = [
        Chunk(
            id="page#1",
            slug="page",
            url="https://s.com/page",
            title="Page",
            text="Section 2 body.",
            score=0.85,
            chunk_index=1,
            total_chunks=3,
        ),
        Chunk(
            id="page#0",
            slug="page",
            url="https://s.com/page",
            title="Page",
            text="Section 1 intro.",
            score=0.90,
            chunk_index=0,
            total_chunks=3,
        ),
        Chunk(
            id="page#2",
            slug="page",
            url="https://s.com/page",
            title="Page",
            text="Section 3 conclusion.",
            score=0.70,
            chunk_index=2,
            total_chunks=3,
        ),
    ]

    merged = merge_adjacent_chunks(chunks)
    assert len(merged) == 1
    c = merged[0]
    assert c.id == "page#0..2"
    assert c.slug == "page"
    assert c.chunk_index == 0
    assert c.score == 0.90
    assert c.text == "Section 1 intro.\n\nSection 2 body.\n\nSection 3 conclusion."


def test_merge_adjacent_chunks_multi_doc_interleaved():
    chunks = [
        Chunk(id="docA#1", slug="docA", url="uA", title="TA", text="A1", score=0.95, chunk_index=1, total_chunks=3),
        Chunk(id="docB#0", slug="docB", url="uB", title="TB", text="B0", score=0.88, chunk_index=0, total_chunks=2),
        Chunk(id="docA#0", slug="docA", url="uA", title="TA", text="A0", score=0.75, chunk_index=0, total_chunks=3),
        Chunk(id="docA#4", slug="docA", url="uA", title="TA", text="A4", score=0.60, chunk_index=4, total_chunks=5),
    ]

    merged = merge_adjacent_chunks(chunks)
    # docA#0 and docA#1 are contiguous -> merged into A0..1 (earliest pos 0, score 0.95)
    # docB#0 is standalone -> pos 1, score 0.88
    # docA#4 is standalone -> pos 3, score 0.60
    assert len(merged) == 3
    assert merged[0].id == "docA#0..1"
    assert merged[0].text == "A0\n\nA1"
    assert merged[0].score == 0.95

    assert merged[1].id == "docB#0"
    assert merged[1].text == "B0"

    assert merged[2].id == "docA#4"
    assert merged[2].text == "A4"


def test_merge_adjacent_chunks_passthrough_unknown_indices():
    chunks = [
        Chunk(id="1", slug="", url="u1", title="T1", text="raw1", score=0.9),
        Chunk(id="2", slug="", url="u2", title="T2", text="raw2", score=0.8),
    ]
    merged = merge_adjacent_chunks(chunks)
    assert len(merged) == 2
    assert merged[0].text == "raw1"
    assert merged[1].text == "raw2"


# ── 5. retrieve() integration and backward compatibility ───────────────────


def test_retrieve_expand_window_integration():
    query_result = {
        "ids": [["doc#1"]],
        "documents": [["Middle section text"]],
        "metadatas": [[
            {"slug": "doc", "url": "https://s.com/doc", "title": "Doc Title", "chunk_index": 1, "total_chunks": 3}
        ]],
        "distances": [[0.1]],
    }
    get_result = {
        "ids": ["doc#0", "doc#2"],
        "documents": ["Intro statistics text", "Conclusion summary text"],
        "metadatas": [
            {"slug": "doc", "url": "https://s.com/doc", "title": "Doc Title", "chunk_index": 0, "total_chunks": 3},
            {"slug": "doc", "url": "https://s.com/doc", "title": "Doc Title", "chunk_index": 2, "total_chunks": 3},
        ],
    }

    col = _FakeCollection(query_result=query_result, get_result=get_result)
    store = _store(col)
    embedder = _FakeEmbedder()

    # With expand_window=True: chunks 0, 1, 2 are retrieved and merged together
    chunks = retrieve(
        "tell me about intro statistics and conclusion",
        k=1,
        expand_window=True,
        window_size=1,
        embedder=embedder,
        store=store,
    )
    assert len(chunks) == 1
    assert chunks[0].id == "doc#0..2"
    assert "Intro statistics text" in chunks[0].text
    assert "Middle section text" in chunks[0].text
    assert "Conclusion summary text" in chunks[0].text


def test_retrieve_backward_compatibility_defaults():
    query_result = {
        "ids": [["doc#1", "doc#2"]],
        "documents": [["Mid text", "Tail text"]],
        "metadatas": [[
            {"slug": "doc", "url": "https://s.com/doc", "title": "Doc", "chunk_index": 1, "total_chunks": 3},
            {"slug": "doc", "url": "https://s.com/doc", "title": "Doc", "chunk_index": 2, "total_chunks": 3},
        ]],
        "distances": [[0.1, 0.2]],
    }
    col = _FakeCollection(query_result=query_result)
    store = _store(col)
    embedder = _FakeEmbedder()

    # Standard retrieve call without expand_window/merge_adjacent maintains exact top-k chunks
    chunks = retrieve("sample query", k=2, embedder=embedder, store=store)
    assert len(chunks) == 2
    assert chunks[0].id == "doc#1"
    assert chunks[1].id == "doc#2"
