"""VectorStore tolerates metadata rows missing a key.

Chroma metadata was indexed with `m["slug"]` / `m["content_hash"]`, so a row
written by a partial or older ingest raised KeyError — out of an ingest, or
mid-chat-turn as a 502. A missing key means "treat this page as unknown", which
makes it re-embed; it is never a reason to abort.
"""

from __future__ import annotations

from stratpoint_rag.rag.store import VectorStore


class _FakeCollection:
    """Stands in for a Chroma collection returning imperfect metadata."""

    def __init__(self, metadatas, query_result=None):
        self._metadatas = metadatas
        self._query_result = query_result

    def get(self, **kwargs):
        return {"metadatas": self._metadatas}

    def query(self, **kwargs):
        return self._query_result


def _store(collection) -> VectorStore:
    store = VectorStore.__new__(VectorStore)  # bypass the Chroma client
    store.col = collection
    return store


def test_slugs_skips_rows_without_a_slug():
    store = _store(_FakeCollection([{"slug": "a"}, {}, None, {"url": "u"}, {"slug": "b"}]))
    assert store.slugs() == {"a", "b"}


def test_stored_hash_returns_none_when_the_key_is_absent():
    """None means 'unknown', which makes the ingest re-embed the page."""
    assert _store(_FakeCollection([{"slug": "a"}])).stored_hash("a") is None


def test_stored_hash_still_reads_a_present_hash():
    store = _store(_FakeCollection([{"slug": "a", "content_hash": "deadbeef"}]))
    assert store.stored_hash("a") == "deadbeef"


def test_stored_hash_handles_no_rows():
    assert _store(_FakeCollection([])).stored_hash("missing") is None


def test_query_tolerates_partial_metadata():
    result = {
        "documents": [["body one", "body two"]],
        "metadatas": [[{"slug": "a", "url": "u", "title": "T"}, {}]],
        "distances": [[0.1, 0.2]],
    }
    chunks = _store(_FakeCollection([], result)).query([0.0], k=2)

    assert len(chunks) == 2
    assert chunks[0].slug == "a" and chunks[0].title == "T"
    # The incomplete row degrades to empty strings rather than raising.
    assert chunks[1].slug == "" and chunks[1].url == "" and chunks[1].title == ""
    assert chunks[1].text == "body two"
