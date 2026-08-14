"""Unit tests for citation URL deduplication (Issue A).

Verifies that:
- Duplicate citations with identical URLs are deduplicated.
- Duplicate citations with trailing slash differences (e.g. /foo/ vs /foo) are deduplicated.
- Distinct URLs are preserved in order.
- answer_grounded() output text and parsed.citations contain only deduplicated citations.
"""
from __future__ import annotations

import json
import httpx
import pytest
import respx

from stratpoint_rag.prompts.schema import Citation, GroundedAnswer
from stratpoint_rag.rag import answer as answer_mod
from stratpoint_rag.rag.models import Chunk

_NIM_URL = "https://integrate.api.nvidia.com/v1/chat/completions"


def _stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(answer_mod.config, "nvidia_api_key", lambda: "test-key")
    monkeypatch.setattr(
        answer_mod,
        "retrieve",
        lambda q, k: [
            Chunk(
                id="1",
                slug="s",
                url="https://stratpoint.com/s",
                title="t",
                text="ctx",
            )
        ],
    )


def _payload(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


# ── Unit tests for _dedupe_citations ──────────────────────────────────────────


def test_dedupe_citations_identical_urls():
    citations = [
        Citation(url="https://stratpoint.com/cloud", title="Cloud Services"),
        Citation(url="https://stratpoint.com/cloud", title="Cloud Services (dup)"),
    ]
    deduped = answer_mod._dedupe_citations(citations)
    assert len(deduped) == 1
    assert deduped[0].url == "https://stratpoint.com/cloud"
    assert deduped[0].title == "Cloud Services"


def test_dedupe_citations_trailing_slash():
    citations = [
        Citation(url="https://stratpoint.com/foo/", title="Foo Slash"),
        Citation(url="https://stratpoint.com/foo", title="Foo No Slash"),
    ]
    deduped = answer_mod._dedupe_citations(citations)
    assert len(deduped) == 1
    assert deduped[0].url == "https://stratpoint.com/foo/"
    assert deduped[0].title == "Foo Slash"


def test_dedupe_citations_trailing_slash_reverse_order():
    citations = [
        Citation(url="https://stratpoint.com/foo", title="Foo No Slash"),
        Citation(url="https://stratpoint.com/foo/", title="Foo Slash"),
    ]
    deduped = answer_mod._dedupe_citations(citations)
    assert len(deduped) == 1
    assert deduped[0].url == "https://stratpoint.com/foo"
    assert deduped[0].title == "Foo No Slash"


def test_dedupe_citations_whitespace_normalization():
    citations = [
        Citation(url="  https://stratpoint.com/bar/  ", title="Bar 1"),
        Citation(url="https://stratpoint.com/bar", title="Bar 2"),
    ]
    deduped = answer_mod._dedupe_citations(citations)
    assert len(deduped) == 1
    assert deduped[0].title == "Bar 1"


def test_dedupe_citations_preserves_distinct_order():
    citations = [
        Citation(url="https://stratpoint.com/first", title="First"),
        Citation(url="https://stratpoint.com/second", title="Second"),
        Citation(url="https://stratpoint.com/first/", title="First Duplicate"),
        Citation(url="https://stratpoint.com/third", title="Third"),
        Citation(url="https://stratpoint.com/second", title="Second Duplicate"),
    ]
    deduped = answer_mod._dedupe_citations(citations)
    assert len(deduped) == 3
    assert [c.url for c in deduped] == [
        "https://stratpoint.com/first",
        "https://stratpoint.com/second",
        "https://stratpoint.com/third",
    ]
    assert [c.title for c in deduped] == ["First", "Second", "Third"]


def test_dedupe_citations_empty_list():
    assert answer_mod._dedupe_citations([]) == []


def test_dedupe_citations_empty_url_falls_back_to_title():
    citations = [
        Citation(url="", title="About Stratpoint"),
        Citation(url="", title="About Stratpoint"),
        Citation(url="", title="Contact Us"),
    ]
    deduped = answer_mod._dedupe_citations(citations)
    assert len(deduped) == 2
    assert [c.title for c in deduped] == ["About Stratpoint", "Contact Us"]


# ── Integration tests for answer_grounded with duplicate citations ───────────


@respx.mock
def test_answer_grounded_dedupes_citations_in_model_and_sources_footer(monkeypatch):
    _stub(monkeypatch)

    duplicate_citations = [
        {
            "title": "Stratpoint CEO: More Women Should Be in IT | Stratpoint Blog",
            "url": "https://stratpoint.com/2021/03/29/more-women-should-be-in-it/",
        },
        {
            "title": "Stratpoint CEO: More Women Should Be in IT | Stratpoint Blog",
            "url": "https://stratpoint.com/2021/03/29/more-women-should-be-in-it/",
        },
        {
            "title": "Stratpoint CEO: More Women Should Be in IT | Stratpoint Blog",
            "url": "https://stratpoint.com/2021/03/29/more-women-should-be-in-it",
        },
        {
            "title": "Stratpoint CEO MR Dela Cruz on ANC Market Edge | Stratpoint",
            "url": "https://stratpoint.com/2021/09/30/stratpoint-ceo-mr-dela-cruz-on-anc-market-edge-2/",
        },
    ]

    body = {
        "answer": "According to Stratpoint CEO MR dela Cruz, more women should be in IT.",
        "citations": duplicate_citations,
        "is_grounded": True,
        "confidence": 1.0,
    }

    respx.post(_NIM_URL).mock(
        return_value=httpx.Response(200, json=_payload(json.dumps(body)))
    )

    text, chunks, grounded, reasoning = answer_mod.answer_grounded("query")

    assert grounded is not None
    # Model citations should be deduplicated (4 down to 2)
    assert len(grounded.citations) == 2
    assert grounded.citations[0].url == "https://stratpoint.com/2021/03/29/more-women-should-be-in-it/"
    assert grounded.citations[1].url == "https://stratpoint.com/2021/09/30/stratpoint-ceo-mr-dela-cruz-on-anc-market-edge-2/"

    # Text footer should contain exactly one bullet for each unique source
    assert text.count("https://stratpoint.com/2021/03/29/more-women-should-be-in-it/") == 1
    assert text.count("https://stratpoint.com/2021/09/30/stratpoint-ceo-mr-dela-cruz-on-anc-market-edge-2/") == 1
    assert "Sources used:\n- Stratpoint CEO: More Women Should Be in IT" in text
    assert "- Stratpoint CEO MR Dela Cruz on ANC Market Edge" in text
