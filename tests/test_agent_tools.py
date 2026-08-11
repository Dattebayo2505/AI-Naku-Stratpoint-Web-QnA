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
    out = tools.find_resource("cloud")
    assert "- AWS whitepaper (https://aws.com/wp.pdf)" in out


def test_find_resource_reports_when_none(monkeypatch):
    chunk = Chunk(id="1", slug="s", url="u", title="P", text="no links here", score=0.1)
    monkeypatch.setattr(tools, "_retrieve", lambda topic, k=5: [chunk])
    assert "No downloadable resources" in tools.find_resource("cloud")


def test_search_stratpoint_delegates_to_rag_answer(monkeypatch):
    monkeypatch.setattr(
        tools, "_rag_answer_grounded", lambda q: ("grounded answer for " + q, [], None, None)
    )
    assert tools.search_stratpoint("services") == "grounded answer for services"


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
    tools.search_stratpoint("x")
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
    tools.find_resource("cloud")
    assert tools.captured_chunks() == [chunk]


def test_capture_is_noop_without_begin(monkeypatch):
    """Recording is inert when begin_capture() was never called (direct calls)."""
    chunk = Chunk(id="1", slug="s", url="u", title="P", text="no links", score=0.1)
    monkeypatch.setattr(tools, "_retrieve", lambda topic, k=5: [chunk])
    # No begin_capture() here.
    tools.find_resource("cloud")
    assert tools.captured_chunks() == []


def test_find_resource_uses_higher_recall_k(monkeypatch):
    """Regression: resource discovery needs recall margin, so retrieve with k>=10
    (a single link-bearing chunk is easily pushed past k=5 by topic rephrasing)."""
    seen = {}

    def fake_retrieve(topic, k=5):
        seen["k"] = k
        return []

    monkeypatch.setattr(tools, "_retrieve", fake_retrieve)
    tools.find_resource("anything")
    assert seen["k"] >= 10


def test_tools_are_plain_callables():
    """No @tool wrapper: the loop dispatches by calling the function directly."""
    assert callable(tools.search_stratpoint)
    assert not hasattr(tools.search_stratpoint, "invoke")


def test_registry_maps_names_to_functions():
    registry = tools.build_tool_registry(tools.build_tool_specs())
    assert registry["search_stratpoint"] is tools.search_stratpoint
    assert registry["find_resource"] is tools.find_resource
    assert set(registry) == {
        "search_stratpoint",
        "find_resource",
        "estimate_cost_and_timeline",
        "generate_proposal_pdf",
    }


def test_specs_carry_arg_names_for_the_trace():
    """tool_input keys must stay 'query'/'topic' so the UI debug panel renders
    the same JSON it did under native function-calling."""
    by_name = {s.name: s for s in tools.build_tool_specs()}
    assert by_name["search_stratpoint"].arg == "query"
    assert by_name["find_resource"].arg == "topic"


def test_find_resource_description_keeps_the_full_wording_rule():
    """Load-bearing: tuned live on llama. Shortening the topic to keywords
    misses the document that mentions it."""
    by_name = {s.name: s for s in tools.build_tool_specs()}
    desc = by_name["find_resource"].description
    assert "FULL" in desc
    assert "figures and years" in desc


def test_search_stratpoint_description_marks_it_the_default():
    by_name = {s.name: s for s in tools.build_tool_specs()}
    assert "default" in by_name["search_stratpoint"].description.lower()


# ── capture nesting ────────────────────────────────────────────────────────
#
# run_with_guardrails opens a capture, then run_react opens its own and closes
# it in a finally. Both run in one context, so a non-counting end_capture reset
# the sinks before the outer caller read them and every outer read came back
# empty — taking the output hallucination check's source chunks and the
# is_grounded/confidence metadata with it.


def test_capture_survives_a_nested_scope(monkeypatch):
    """An inner begin/end pair must not clear the outer caller's sinks."""
    chunk = Chunk(id="1", slug="s", url="u", title="P", text="body", score=0.9)
    monkeypatch.setattr(tools, "_retrieve", lambda topic, k=5: [chunk])

    tools.begin_capture()          # outer, e.g. run_with_guardrails
    try:
        tools.begin_capture()      # inner, e.g. run_react
        try:
            tools.find_resource("cloud")
        finally:
            tools.end_capture()    # inner close must NOT wipe the sink

        assert tools.captured_chunks() == [chunk]
    finally:
        tools.end_capture()

    # The outermost close still resets.
    assert tools.captured_chunks() == []


def test_proposal_sink_is_not_reinitialised_by_a_nested_begin():
    """A nested begin_capture must not discard what the outer scope collected."""
    from stratpoint_rag.agent.contracts import EstimationResult

    est = EstimationResult(
        total_cost_usd=1.0, estimated_weeks=1.0, role_breakdown=[],
        phase_timeline=[], summary="s",
    )
    tools.begin_capture()
    try:
        tools._update_proposal_data(estimation=est)
        tools.begin_capture()
        tools.end_capture()
        assert tools.captured_proposal_data().estimation is est
    finally:
        tools.end_capture()


def test_unpaired_end_capture_is_a_hard_reset(monkeypatch):
    """Depth clamps at zero, so the test fixtures' bare end_capture() still
    works as a reset rather than driving the counter negative."""
    chunk = Chunk(id="1", slug="s", url="u", title="P", text="body", score=0.9)
    monkeypatch.setattr(tools, "_retrieve", lambda topic, k=5: [chunk])

    tools.end_capture()
    tools.end_capture()
    tools.end_capture()

    tools.begin_capture()
    tools.find_resource("cloud")
    assert tools.captured_chunks() == [chunk]
    tools.end_capture()


def test_estimation_observation_labels_amounts_with_its_own_currency(monkeypatch):
    """The Observation the loop reads must not call a peso figure dollars.

    Measured before the fix: one Observation carried both 'a total investment of
    PHP 1,379,994.00' and 'Total Cost: $1,379,994.00 USD' — the same number
    under two currencies 60x apart, and the prompt tells the loop to quote it.
    """
    from stratpoint_rag.docparse.schema import ExtractedRequirements

    tools.begin_capture()
    try:
        tools._update_proposal_data(
            requirements=ExtractedRequirements(
                features=["Shopping cart"], currency_symbol="₱", currency_code="PHP"
            )
        )
        obs = tools._wrap_estimate_cost_and_timeline('{"features": ["Shopping cart"]}')
    finally:
        tools.end_capture()

    assert "PHP" in obs
    assert "USD" not in obs
    assert "$" not in obs
