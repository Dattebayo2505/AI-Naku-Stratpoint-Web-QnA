import pytest

from stratpoint_rag.agent import tools
from stratpoint_rag.rag.models import Chunk


@pytest.fixture(autouse=True)
def _reset_capture():
    """ContextVars persist across tests in the shared context; reset the capture
    sinks around each test so recording state doesn't leak between them."""
    tools.end_capture()
    yield
    tools.end_capture()


def test_extract_doc_links_finds_pdf_and_strips_markdown():
    text = "see [**35% cost reduction**](https://pages.awscloud.com/x/the-value.pdf) now"
    assert tools._extract_doc_links(text) == [
        ("35% cost reduction", "https://pages.awscloud.com/x/the-value.pdf")
    ]


def test_extract_doc_links_dedupes_and_ignores_non_pdfs():
    """Office docs are deliberately not matched — the corpus contains none."""
    text = (
        "[a](https://s.com/f.pdf) [b](https://s.com/f.pdf) "
        "[c](https://s.com/page) [d](https://s.com/deck.pptx) "
        "[e](https://s.com/paper.docx)"
    )
    assert tools._extract_doc_links(text) == [("a", "https://s.com/f.pdf")]


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
    monkeypatch.setattr(
        tools, "_rag_answer_grounded", lambda q: ("grounded answer for " + q, [], None, None)
    )
    assert tools.search_stratpoint.invoke("services") == "grounded answer for services"


def test_search_stratpoint_records_chunks_and_grounded(monkeypatch):
    """Fix B/C: the search tool surfaces its chunks + grounded metadata to the
    capture sink so the guardrail layer can verify the agent's answer."""
    from stratpoint_rag.prompts.schema import GroundedAnswer

    chunk = Chunk(id="1", slug="s", url="u", title="P", text="body", score=0.9)
    grounded = GroundedAnswer(
        answer="a", citations=[], is_grounded=True, confidence=0.8
    )
    monkeypatch.setattr(tools, "_rag_answer_grounded", lambda q: ("a", [chunk], grounded, None))

    tools.begin_capture()
    tools.search_stratpoint.invoke("x")
    assert tools.captured_chunks() == [chunk]
    assert tools.captured_grounded() == [grounded]


def test_find_resource_records_chunks(monkeypatch):
    """Fix B: find_resource surfaces its retrieved chunks to the capture sink."""
    chunk = Chunk(
        id="1", slug="s", url="https://stratpoint.com/p", title="P",
        text="[x](https://s.com/f.pdf)", score=0.9,
    )
    monkeypatch.setattr(tools, "_retrieve", lambda topic, k=5: [chunk])

    tools.begin_capture()
    tools.find_resource.invoke("cloud")
    assert tools.captured_chunks() == [chunk]


def test_capture_is_noop_without_begin(monkeypatch):
    """Recording is inert when begin_capture() was never called (direct calls)."""
    chunk = Chunk(id="1", slug="s", url="u", title="P", text="no links", score=0.1)
    monkeypatch.setattr(tools, "_retrieve", lambda topic, k=5: [chunk])
    # No begin_capture() here.
    tools.find_resource.invoke("cloud")
    assert tools.captured_chunks() == []


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
