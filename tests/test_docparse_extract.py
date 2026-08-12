"""Hop 2 offline: the one-shot/map-reduce boundary, the merge, provenance.

Every test here runs against a ``FakeTextClient`` — no network, no key. That is
the whole point of the ``TextClient`` Protocol existing since hop 1.
"""

import json

import pytest

from stratpoint_rag.docparse import extract
from stratpoint_rag.docparse.models import BriefRef
from stratpoint_rag.docparse.schema import MAX_NOTES, MAX_NOTE_CHARS


class FakeTextClient:
    """Returns canned replies in order and records every prompt it was sent."""

    def __init__(self, *replies, usage=None):
        self._replies = list(replies)
        self.calls = []
        self._usage = usage or {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }

    def complete(self, system, user):
        self.calls.append({"system": system, "user": user})
        if not self._replies:
            raise AssertionError("complete() called more times than the script allows")
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply, dict(self._usage)


def payload(**over):
    body = {
        "target_platform": [],
        "features": [],
        "constraints": [],
        "tech_stack": [],
        "complexity": "medium",
        "extraction_notes": [],
    }
    body.update(over)
    return json.dumps(body)


def doc(pages, *, body="Some requirement text that is reasonably long.\n"):
    """Build a hop-1-shaped transcription with `pages` page blocks."""
    blocks = [
        "---",
        "source_file: brief.pdf",
        "sha256: deadbeef",
        f"pages_total: {pages}",
        f"pages_parsed: {pages}",
        "pages_failed: []",
        "---",
    ]
    for n in range(1, pages + 1):
        blocks.append(f"\n## Page {n}\n<!-- page {n} | source: text -->\n{body}")
    return "\n".join(blocks)


@pytest.fixture(autouse=True)
def _clear_cache():
    extract.clear_cache()
    yield
    extract.clear_cache()


# ── frontmatter and page splitting ──────────────────────────────────────────


def test_frontmatter_is_not_sent_to_the_model():
    """It is provenance we already hold structurally, and 'pages_failed: [7]'
    in the prompt invites the model to treat it as a requirement."""
    client = FakeTextClient(payload())
    extract.extract_requirements(doc(2), text=client)

    sent = client.calls[0]["user"]
    assert "sha256" not in sent
    assert "deadbeef" not in sent


def test_a_document_with_no_page_wrapper_is_one_block():
    assert extract._split_pages("just some prose") == [(1, "just some prose")]


def test_page_numbers_come_from_the_wrapper_not_from_counting():
    """Hop 1 emits exact 1-based numbers; a truncated or partial artifact must
    keep them rather than be renumbered from zero."""
    md = "## Page 3\nthird\n\n## Page 4\nfourth\n"
    assert [n for n, _ in extract._split_pages(md)] == [3, 4]


# ── the one-shot / map-reduce boundary ──────────────────────────────────────


def test_a_small_brief_is_one_call():
    client = FakeTextClient(payload(features=["SSO"]))
    result = extract.extract_requirements(doc(3), text=client)

    assert len(client.calls) == 1
    assert result.features == ["SSO"]


def test_a_large_brief_switches_to_map_reduce(monkeypatch):
    monkeypatch.setenv("DOCPARSE_EXTRACTION_TOKEN_BUDGET", "50")
    client = FakeTextClient(payload(), payload(), payload())

    extract.extract_requirements(doc(12), text=client)

    # 12 pages / 5 per group = 3 groups.
    assert len(client.calls) == 3


def test_a_single_page_never_map_reduces(monkeypatch):
    """There is nothing to split. Grouping a lone page would just be one call
    with a misleading 'this is pages 1-1 of a longer brief' scope line."""
    monkeypatch.setenv("DOCPARSE_EXTRACTION_TOKEN_BUDGET", "1")
    client = FakeTextClient(payload())

    extract.extract_requirements(doc(1), text=client)

    assert len(client.calls) == 1
    assert "complete client brief" in client.calls[0]["user"]


def test_group_arithmetic_covers_every_page_exactly_once():
    pages = [(n, f"p{n}") for n in range(1, 13)]
    groups = extract._group_pages(pages, 5)

    assert [(a, b) for a, b, _ in groups] == [(1, 5), (6, 10), (11, 12)]
    seen = " ".join(text for _, _, text in groups)
    for n in range(1, 13):
        assert f"p{n}" in seen


def test_group_prompts_say_which_pages_they_cover(monkeypatch):
    monkeypatch.setenv("DOCPARSE_EXTRACTION_TOKEN_BUDGET", "50")
    client = FakeTextClient(payload(), payload())

    extract.extract_requirements(doc(7), text=client)

    assert "pages 1-5" in client.calls[0]["user"]
    assert "pages 6-7" in client.calls[1]["user"]


# ── the deterministic merge ─────────────────────────────────────────────────


def test_merge_unions_and_dedupes_case_and_whitespace_insensitively(monkeypatch):
    monkeypatch.setenv("DOCPARSE_EXTRACTION_TOKEN_BUDGET", "50")
    client = FakeTextClient(
        payload(features=["User  SSO", "Checkout"]),
        payload(features=["user sso", "Search"]),
    )

    result = extract.extract_requirements(doc(7), text=client)

    # First-seen casing wins; the near-duplicate is dropped.
    assert result.features == ["User  SSO", "Checkout", "Search"]


def test_merge_takes_the_max_complexity_not_the_average(monkeypatch):
    """A brief whose hardest group is 'high' is a high-complexity brief.
    Averaging would quietly under-price it."""
    monkeypatch.setenv("DOCPARSE_EXTRACTION_TOKEN_BUDGET", "50")
    client = FakeTextClient(
        payload(complexity="low"),
        payload(complexity="high"),
        payload(complexity="medium"),
    )

    result = extract.extract_requirements(doc(12), text=client)

    assert result.complexity == "high"


def test_merge_is_pure_python_and_makes_no_extra_call(monkeypatch):
    """The merge must never be a third LLM call: five groups each inventing one
    plausible feature would be laundered into one authoritative-looking list."""
    monkeypatch.setenv("DOCPARSE_EXTRACTION_TOKEN_BUDGET", "50")
    client = FakeTextClient(payload(), payload())

    extract.extract_requirements(doc(7), text=client)

    assert len(client.calls) == 2  # 2 groups, no merge call


def test_non_string_list_items_are_dropped_not_stringified():
    client = FakeTextClient(
        json.dumps(
            {
                "features": ["SSO", {"name": "Checkout"}, None, 42],
                "constraints": [],
                "target_platform": [],
                "tech_stack": [],
                "complexity": "low",
                "extraction_notes": [],
            }
        )
    )

    result = extract.extract_requirements(doc(1), text=client)

    assert result.features == ["SSO", "42"]


# ── the complexity vocabulary ───────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["moderate", "Medium-High", "VERY HIGH", ""])
def test_invented_complexity_values_fall_back_rather_than_raising(bad):
    """The Literal is the boundary, but a whole extraction must not be lost to
    one bad enum — that is a 5-page group of real requirements thrown away."""
    client = FakeTextClient(payload(complexity=bad, features=["SSO"]))

    result = extract.extract_requirements(doc(1), text=client)

    assert result.complexity == "medium"
    assert result.features == ["SSO"]


def test_capitalised_valid_values_are_accepted():
    client = FakeTextClient(payload(complexity="High"))
    assert extract.extract_requirements(doc(1), text=client).complexity == "high"


# ── fences and parse failure ────────────────────────────────────────────────


def test_markdown_fenced_json_is_parsed():
    client = FakeTextClient("```json\n" + payload(features=["SSO"]) + "\n```")

    assert extract.extract_requirements(doc(1), text=client).features == ["SSO"]


def test_unparseable_reply_degrades_to_a_note_instead_of_raising():
    client = FakeTextClient("I'm sorry, I can't help with that.")

    result = extract.extract_requirements(doc(1), text=client)

    assert result.features == []
    assert any("could not be parsed" in n for n in result.extraction_notes)


def test_a_call_that_raises_degrades_to_a_note():
    client = FakeTextClient(RuntimeError("boom"))

    result = extract.extract_requirements(doc(1), text=client)

    assert any("extraction call failed" in n for n in result.extraction_notes)


def test_one_failed_group_does_not_lose_the_others(monkeypatch):
    monkeypatch.setenv("DOCPARSE_EXTRACTION_TOKEN_BUDGET", "50")
    client = FakeTextClient(RuntimeError("boom"), payload(features=["Checkout"]))

    result = extract.extract_requirements(doc(7), text=client)

    assert result.features == ["Checkout"]
    assert any("pages 1-5" in n for n in result.extraction_notes)


def test_an_empty_transcription_makes_no_call_at_all():
    client = FakeTextClient()  # any call raises

    result = extract.extract_requirements("", text=client)

    assert result.features == []
    assert result.extraction_notes == ["the transcription was empty; nothing to extract"]


# ── extraction_notes is length-capped on both axes ──────────────────────────


def test_notes_are_capped_in_count_and_length():
    """The only free-text field the model controls, so the only channel
    injected document content could travel through."""
    client = FakeTextClient(
        payload(extraction_notes=[f"note {i} " + "x" * 500 for i in range(20)])
    )

    notes = extract.extract_requirements(doc(1), text=client).extraction_notes

    assert len(notes) == MAX_NOTES
    assert all(len(n) <= MAX_NOTE_CHARS for n in notes)


def test_our_own_failure_notes_survive_the_cap():
    """'page 7 could not be read' must not be evicted by the model's eighth
    observation about the budget section."""
    client = FakeTextClient("not json at all")

    notes = extract.extract_requirements(doc(1), text=client).extraction_notes

    assert "could not be parsed" in notes[0]


# ── provenance ──────────────────────────────────────────────────────────────


def test_provenance_is_copied_from_hop_one_not_re_derived():
    """The model is never asked for page counts, and the transcription's own
    frontmatter is not re-parsed — the two would drift."""
    client = FakeTextClient(payload())

    result = extract.extract_requirements(
        doc(3),
        provenance={"pages_total": 20, "pages_parsed": 14, "pages_failed": [7, 8]},
        source_markdown_path="/tmp/t.md",
        text=client,
    )

    assert result.pages_total == 20
    assert result.pages_parsed == 14
    assert result.pages_failed == [7, 8]
    assert result.source_markdown_path == "/tmp/t.md"


def test_a_model_supplied_page_count_cannot_reach_the_output():
    client = FakeTextClient(
        json.dumps(
            {
                "features": [],
                "constraints": [],
                "target_platform": [],
                "tech_stack": [],
                "complexity": "low",
                "extraction_notes": [],
                "pages_total": 999,
                "pages_parsed": 999,
                "client_name": "Injected Corp",
            }
        )
    )

    result = extract.extract_requirements(
        doc(1), provenance={"pages_total": 3, "pages_parsed": 3}, text=client
    )

    assert result.pages_total == 3
    assert result.pages_parsed == 3
    assert not hasattr(result, "client_name")


def test_the_schema_has_no_name_fields():
    """A required name field is an instruction to hallucinate one."""
    fields = set(extract.ExtractedRequirements.model_fields)
    assert "client_name" not in fields
    assert "project_name" not in fields


# ── the sha256 cache ────────────────────────────────────────────────────────


def _brief(tmp_path, markdown, sha="abc123"):
    path = tmp_path / "transcription.md"
    path.write_text(markdown, encoding="utf-8")
    return BriefRef(
        upload_id="u1",
        filename="brief.pdf",
        sha256=sha,
        markdown_path=str(path),
        pages_total=2,
        pages_parsed=2,
    )


def test_extract_brief_caches_by_sha256(tmp_path):
    """The loop can call the same tool twice in one turn and Streamlit reruns
    constantly; without this a redundant call re-runs map-reduce."""
    brief = _brief(tmp_path, doc(2))
    client = FakeTextClient(payload(features=["SSO"]))

    first = extract.extract_brief(brief, text=client)
    second = extract.extract_brief(brief, text=client)

    assert len(client.calls) == 1
    assert first == second


def test_a_different_hash_is_a_different_extraction(tmp_path):
    a = _brief(tmp_path, doc(2), sha="aaa")
    b = _brief(tmp_path, doc(2), sha="bbb")
    client = FakeTextClient(payload(features=["A"]), payload(features=["B"]))

    assert extract.extract_brief(a, text=client).features == ["A"]
    assert extract.extract_brief(b, text=client).features == ["B"]


def test_extract_brief_carries_hop_one_provenance_through(tmp_path):
    brief = _brief(tmp_path, doc(2))
    result = extract.extract_brief(brief, text=FakeTextClient(payload()))

    assert result.pages_total == 2
    assert result.source_markdown_path == brief.markdown_path


def test_extract_brief_refuses_an_untranscribed_upload():
    brief = BriefRef(upload_id="u1", filename="b.pdf", sha256="x")

    with pytest.raises(FileNotFoundError):
        extract.extract_brief(brief, text=FakeTextClient())


# ── LLMOps ──────────────────────────────────────────────────────────────────


def test_usage_is_accumulated_on_the_calling_thread(monkeypatch):
    """Hop 2 is deliberately not parallelized: running here is what makes
    add_usage() land in the accumulator the request thread actually reads."""
    from stratpoint_rag import llmops

    monkeypatch.setenv("DOCPARSE_EXTRACTION_TOKEN_BUDGET", "50")
    llmops.reset_usage()
    client = FakeTextClient(payload(), payload())

    extract.extract_requirements(doc(7), text=client)

    assert llmops.pop_usage()["total_tokens"] == 30  # 2 calls x 15


# ── currency detection ─────────────────────────────────────────────────────
#
# "PHP" is the programming language far more often than it is pesos in a
# software RFP. The old pattern matched the bare token and returned on the first
# hit, so an explicit "$250,000 USD" lost to a mention of the backend stack —
# and the detected currency drives the x60 conversion in pdf_gen/mapping.py.


@pytest.mark.parametrize(
    "text",
    [
        "Backend must be PHP 8.2 with Laravel.",
        "Budget is $250,000 USD. Stack: PHP/Laravel.",
        "We need PHP developers and a PHP framework.",
        "Rewrite the legacy PHP application.",
    ],
)
def test_php_the_language_is_not_read_as_pesos(text):
    assert extract.detect_currency(text) == ("$", "USD")


@pytest.mark.parametrize(
    "text",
    [
        "Target budget: ₱250,000",
        "Project budget is in pesos (PhP 100,000)",
        "Budget is 600,000 PHP",
        "Total: 1,200,000 pesos",
        "Budget PHP 2,500,000 for a PHP/Laravel rebuild.",  # both senses, one text
    ],
)
def test_real_peso_amounts_are_still_detected(text):
    assert extract.detect_currency(text) == ("₱", "PHP")


@pytest.mark.parametrize(
    "text",
    ["Budget is 500,000 USD", "Target price: $10,000", ""],
)
def test_usd_and_empty_text_default_to_dollars(text):
    assert extract.detect_currency(text) == ("$", "USD")


# ── the hop-2 cache is keyed by bytes, but a path is not a property of bytes ──


def test_cached_extraction_carries_the_callers_own_source_path(tmp_path):
    """Two sessions, identical bytes: each must get its own markdown path back.

    The cache key is the sha256, which is right — identical bytes extract to
    identical requirements. But `source_markdown_path` points inside one
    session's upload directory, and mapping.py read_text()s it when building the
    quote. Returning the first session's path handed the second a file the TTL
    sweep may delete underneath it.
    """
    extract.clear_cache()

    a = tmp_path / "sess_a" / "transcription.md"
    b = tmp_path / "sess_b" / "transcription.md"
    for p in (a, b):
        p.parent.mkdir(parents=True)
        p.write_text("## Page 1\n\nBuild a web portal.", encoding="utf-8")

    def brief(path):
        return BriefRef(
            upload_id="u", filename="f.pdf", sha256="samehash",
            markdown_path=str(path), pages_total=1, pages_parsed=1,
        )

    client = FakeTextClient(payload())
    first = extract.extract_brief(brief(a), text=client)
    second = extract.extract_brief(brief(b), text=client)   # cache hit

    assert first.source_markdown_path == str(a)
    assert second.source_markdown_path == str(b)
    # Still a cache hit: the second call must not have spent another LLM call.
    assert len(client.calls) == 1


def test_cache_hit_still_returns_the_same_extracted_content(tmp_path):
    """Re-stamping the path must not disturb anything else on the model."""
    extract.clear_cache()
    p = tmp_path / "transcription.md"
    p.write_text("## Page 1\n\nBuild a web portal.", encoding="utf-8")
    ref = BriefRef(
        upload_id="u", filename="f.pdf", sha256="h",
        markdown_path=str(p), pages_total=1, pages_parsed=1,
    )

    client = FakeTextClient(payload())
    first = extract.extract_brief(ref, text=client)
    second = extract.extract_brief(ref, text=client)

    assert second.model_dump(exclude={"source_markdown_path"}) == first.model_dump(
        exclude={"source_markdown_path"}
    )


# ── a currency declared in words, and a peso budget quoted in dollars too ──
#
# Requiring an amount beside "PHP" excluded the language, but it also excluded
# every currency *declaration* not written next to a number. A brief whose fee
# table holds bare numbers under "Currency: PHP" scored zero peso matches and
# the client got a dollar-denominated proposal for a peso-budgeted engagement.


@pytest.mark.parametrize(
    "text",
    [
        "Currency: PHP",
        "All amounts are stated in PHP.",
        "All prices in PHP unless noted.",
        "Fees are denominated in PHP.",
        "Total Pricing (PHP)",
    ],
)
def test_a_currency_declared_in_words_is_read_as_pesos(text):
    assert extract.detect_currency(text) == ("₱", "PHP")


def test_a_peso_budget_that_cites_a_dollar_equivalent_stays_pesos():
    """'Any peso signal wins' became a majority vote, which a parenthetical
    dollar equivalent plus a couple of stray '$' can carry."""
    text = (
        "Total budget: PHP 5,000,000 (approximately USD 83,000). "
        "Payment in dollars is not accepted; the $ figure is indicative only."
    )
    assert extract.detect_currency(text) == ("₱", "PHP")


# ── the hop-2 cache must not outlive the transcription it describes ────────


def test_a_re_transcription_of_the_same_upload_is_not_served_the_failed_run(tmp_path):
    """Hop 1 is a vision pipeline: the same bytes do not transcribe identically
    twice. A 20-page scan that lost pages, re-uploaded and read cleanly, must
    not get the lost-page run back out of the cache."""
    extract.clear_cache()

    lossy = tmp_path / "a" / "transcription.md"
    clean = tmp_path / "b" / "transcription.md"
    for p in (lossy, clean):
        p.parent.mkdir(parents=True)
    lossy.write_text(doc(1), encoding="utf-8")
    clean.write_text(doc(2), encoding="utf-8")

    def brief(path, parsed, failed):
        return BriefRef(
            upload_id="u", filename="scan.pdf", sha256="samebytes",
            markdown_path=str(path), pages_total=2,
            pages_parsed=parsed, pages_failed=failed,
        )

    client = FakeTextClient(payload(features=["A"]), payload(features=["B"]))
    first = extract.extract_brief(brief(lossy, 1, [2]), text=client)
    second = extract.extract_brief(brief(clean, 2, []), text=client)

    assert first.pages_failed == [2]
    assert second.pages_failed == []
    assert second.features == ["B"]
    assert len(client.calls) == 2


def test_a_cache_hit_carries_this_callers_own_page_accounting(tmp_path):
    """Lost pages travel with the price: a session that lost pages must not be
    handed a clean session's provenance just because the bytes matched."""
    extract.clear_cache()

    a = tmp_path / "sess_a" / "transcription.md"
    b = tmp_path / "sess_b" / "transcription.md"
    for p in (a, b):
        p.parent.mkdir(parents=True)
        p.write_text(doc(2), encoding="utf-8")

    def brief(path, parsed, failed):
        return BriefRef(
            upload_id="u", filename="f.pdf", sha256="samebytes",
            markdown_path=str(path), pages_total=2,
            pages_parsed=parsed, pages_failed=failed,
        )

    client = FakeTextClient(payload())
    extract.extract_brief(brief(a, 2, []), text=client)
    second = extract.extract_brief(brief(b, 1, [7]), text=client)

    assert second.pages_failed == [7]
    assert second.pages_parsed == 1
    assert len(client.calls) == 1  # still a cache hit
