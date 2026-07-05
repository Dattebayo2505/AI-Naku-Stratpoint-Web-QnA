from stratpoint_rag.agent import tools
from stratpoint_rag.rag.models import Chunk


def test_extract_doc_links_finds_pdf_and_strips_markdown():
    text = "see [**35% cost reduction**](https://pages.awscloud.com/x/the-value.pdf) now"
    assert tools._extract_doc_links(text) == [
        ("35% cost reduction", "https://pages.awscloud.com/x/the-value.pdf")
    ]


def test_extract_doc_links_dedupes_and_ignores_non_docs():
    text = (
        "[a](https://s.com/f.pdf) [b](https://s.com/f.pdf) "
        "[c](https://s.com/page) [d](https://s.com/deck.pptx)"
    )
    assert tools._extract_doc_links(text) == [
        ("a", "https://s.com/f.pdf"),
        ("d", "https://s.com/deck.pptx"),
    ]


def test_find_resource_lists_links_from_retrieved_chunks(monkeypatch):
    chunk = Chunk(
        id="1", slug="s", url="https://stratpoint.com/p", title="P",
        text="ref [AWS whitepaper](https://aws.com/wp.pdf)", score=0.9,
    )
    monkeypatch.setattr(tools, "_retrieve", lambda topic, k=5: [chunk])
    out = tools.find_resource.invoke("cloud")
    assert "- AWS whitepaper (https://aws.com/wp.pdf)" in out


def test_find_resource_reports_when_none(monkeypatch):
    chunk = Chunk(id="1", slug="s", url="u", title="P", text="no links here", score=0.1)
    monkeypatch.setattr(tools, "_retrieve", lambda topic, k=5: [chunk])
    assert "No downloadable resources" in tools.find_resource.invoke("cloud")


def test_search_stratpoint_delegates_to_rag_answer(monkeypatch):
    monkeypatch.setattr(tools, "_rag_answer", lambda q: ("grounded answer for " + q, []))
    assert tools.search_stratpoint.invoke("services") == "grounded answer for services"


def test_find_resource_uses_higher_recall_k(monkeypatch):
    """Regression: resource discovery needs recall margin, so retrieve with k>=10
    (a single link-bearing chunk is easily pushed past k=5 by topic rephrasing)."""
    seen = {}

    def fake_retrieve(topic, k=5):
        seen["k"] = k
        return []

    monkeypatch.setattr(tools, "_retrieve", fake_retrieve)
    tools.find_resource.invoke("anything")
    assert seen["k"] >= 10
