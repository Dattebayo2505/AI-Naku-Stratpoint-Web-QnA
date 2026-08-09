"""Talking about an attached brief WITHOUT asking for a proposal.

Regression suite for a live transcript in which every one of these failed at
once. The visitor uploaded a brief and asked:

    "Not asking about a proposal, give me a 2 sentence brief of what the
     document I gave you is about."

and was answered with the naming question; their follow-up, "What document did
I just give you", came back as a $27,030 quote with a PDF link. Four separate
defects chained:

1. ``\\bproposal\\b`` matched the visitor's *negation* of it, so declining a
   proposal was the most reliable way to request one;
2. once the naming ask was in flight the next message was consumed as its
   answer unconditionally, discarding it and replaying the original request;
3. a reply that was plainly not a name was stored as ``client_name`` anyway —
   the visitor's question would have been printed on the proposal;
4. the ReAct loop had no non-proposal mode: its system prompt cast every turn
   as proposal work, and no tool could reach the document's prose at all.

The four sections below correspond, in order.
"""

import pytest

from stratpoint_rag.agent import guardrail_agent as ga
from stratpoint_rag.agent import tools as agent_tools
from stratpoint_rag.agent.models import AgentResult
from stratpoint_rag.agent.react import render_system_prompt
from stratpoint_rag.disambiguation import classifier, engagement
from stratpoint_rag.disambiguation.classifier import classify
from stratpoint_rag.disambiguation.schemas import IntentCategory, IntentQuery
from stratpoint_rag.docparse import BriefRef

SESSION = "nonproposal"

# The visitor's exact words.
ASKED_ABOUT_THE_DOC = (
    "Not asking about a proposal, give me a 2 sentence brief of what the "
    "document I gave you is about."
)
ASKED_WHICH_DOC = "What document did I just give you"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    ga.clear_memory(SESSION)
    monkeypatch.setattr(ga, "_run_output_guardrails", lambda t, c, cfg, n: (t, None))
    # Offline and deterministic: the classifier escalates anything under 0.7 to
    # a live endpoint. Pinning it to None exercises the heuristic path these
    # tests are about; the LLM path has its own guard, asserted separately in
    # test_the_llm_may_not_overturn_an_explicit_refusal.
    monkeypatch.setattr(classifier, "_llm_classify", lambda text: None)
    yield
    ga.clear_memory(SESSION)


@pytest.fixture
def transcribed(tmp_path):
    path = tmp_path / "transcription.md"
    path.write_text(
        "## Page 1\nClient: Northwind Retail\nProject: Loyalty App\n\n"
        "Northwind wants a customer loyalty mobile app with points and tiers.\n",
        encoding="utf-8",
    )
    return [
        BriefRef(
            upload_id="a3f9c2",
            filename="client-brief.pdf",
            sha256="sha",
            markdown_path=str(path),
            pages_total=12,
            pages_parsed=12,
        )
    ]


def _capture_run_agent(monkeypatch):
    seen = {}

    def fake(message, history=None, *, chat=None, enable_reasoning=False, **kw):
        seen["message"] = message
        seen.update(kw)
        return AgentResult(answer="ok")

    monkeypatch.setattr(ga, "run_agent", fake)
    return seen


# ── 1. the negation ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        ASKED_ABOUT_THE_DOC,
        "not a proposal, just tell me what this says",
        "I don't want a proposal yet",
        "no proposal please, summarize the brief",
        "without a proposal, what does the document ask for?",
    ],
)
def test_a_negated_proposal_mention_is_not_a_proposal_request(message):
    """Saying 'not a proposal' must not be the most reliable way to get one."""
    assert classify(message).intent != IntentCategory.REQUEST_PROPOSAL


@pytest.mark.parametrize(
    "message",
    [
        "can you put together a proposal?",
        "give me a quote",
        "I need a proposal for this brief",
    ],
)
def test_a_real_proposal_request_still_classifies(message):
    """The negation guard must not blunt the intent it guards."""
    assert classify(message).intent == IntentCategory.REQUEST_PROPOSAL


def test_the_llm_may_not_overturn_an_explicit_refusal(monkeypatch):
    """Under 0.7 the classifier escalates to the LLM, which is handed the raw
    message and reads 'proposal' as the salient token — returning exactly the
    intent the visitor took the trouble to rule out."""
    monkeypatch.setattr(
        classifier,
        "_llm_classify",
        lambda text: IntentQuery(
            intent=IntentCategory.REQUEST_PROPOSAL, confidence=0.99, reasoning="llm"
        ),
    )

    assert classify("I don't want a proposal yet").intent != (
        IntentCategory.REQUEST_PROPOSAL
    )


def test_asking_what_the_document_is_does_not_trigger_the_naming_question(
    monkeypatch, transcribed
):
    """The head of the reported transcript."""
    _capture_run_agent(monkeypatch)

    result = ga.run_with_guardrails(
        ASKED_ABOUT_THE_DOC, session_id=SESSION, use_nemo=False, briefs=transcribed
    )

    assert result.guardrail_reason != "Asked how to name the proposal"
    assert engagement.get(SESSION).loop is None


# ── 2. the ask must not swallow the next message ────────────────────────────


def test_a_question_during_the_naming_ask_is_answered_not_swallowed(
    monkeypatch, transcribed
):
    """The tail of the reported transcript: the follow-up question was dropped
    and the original request replayed, which is why a quote came back."""
    seen = _capture_run_agent(monkeypatch)

    ga.run_with_guardrails(
        "can you put together a proposal?", session_id=SESSION, use_nemo=False,
        briefs=transcribed,
    )
    assert engagement.get(SESSION).loop is not None  # ask is in flight

    ga.run_with_guardrails(
        ASKED_WHICH_DOC, session_id=SESSION, use_nemo=False, briefs=transcribed
    )

    assert seen["message"] == ASKED_WHICH_DOC
    assert engagement.get(SESSION).loop is None  # ask abandoned, not left pending


def test_a_non_answer_is_never_stored_as_a_client_name(monkeypatch, transcribed):
    """It would have been printed on the proposal heading."""
    seen = _capture_run_agent(monkeypatch)

    ga.run_with_guardrails(
        "can you put together a proposal?", session_id=SESSION, use_nemo=False,
        briefs=transcribed,
    )
    ga.run_with_guardrails(
        ASKED_WHICH_DOC, session_id=SESSION, use_nemo=False, briefs=transcribed
    )

    assert seen["names"] == (None, None)
    assert engagement.get(SESSION).client_name is None


def test_an_abandoned_ask_is_not_recorded_as_answered(monkeypatch, transcribed):
    """They never answered, so a later proposal request may still ask."""
    _capture_run_agent(monkeypatch)

    ga.run_with_guardrails(
        "can you put together a proposal?", session_id=SESSION, use_nemo=False,
        briefs=transcribed,
    )
    ga.run_with_guardrails(
        ASKED_WHICH_DOC, session_id=SESSION, use_nemo=False, briefs=transcribed
    )

    assert engagement.needs_ask(SESSION)


# ── 3. real answers still work ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("Northwind Retail", ("Northwind Retail", None)),
        ("yes", ("Northwind Retail", "Loyalty App")),
        ("skip", (None, None)),
        ("client is Acme, project is Falcon", ("Acme", "Falcon")),
    ],
)
def test_a_genuine_answer_is_still_consumed_and_the_request_replayed(
    monkeypatch, transcribed, answer, expected
):
    seen = _capture_run_agent(monkeypatch)

    ga.run_with_guardrails(
        "put together a proposal", session_id=SESSION, use_nemo=False,
        briefs=transcribed,
    )
    ga.run_with_guardrails(
        answer, session_id=SESSION, use_nemo=False, briefs=transcribed
    )

    assert seen["message"] == "put together a proposal"
    assert seen["names"] == expected


# ── 4. the loop gets a non-proposal mode, and a tool that can read prose ────


def test_a_plain_question_about_a_brief_does_not_run_in_proposal_mode(
    monkeypatch, transcribed
):
    seen = _capture_run_agent(monkeypatch)

    ga.run_with_guardrails(
        ASKED_ABOUT_THE_DOC, session_id=SESSION, use_nemo=False, briefs=transcribed
    )

    assert seen["proposal_mode"] is False


def test_a_proposal_request_still_runs_in_proposal_mode(monkeypatch, transcribed):
    seen = _capture_run_agent(monkeypatch)

    ga.run_with_guardrails(
        "put together a proposal", session_id=SESSION, use_nemo=False,
        briefs=transcribed,
    )
    ga.run_with_guardrails(
        "skip", session_id=SESSION, use_nemo=False, briefs=transcribed
    )

    assert seen["proposal_mode"] is True


def test_the_non_proposal_prompt_does_not_demand_cost_and_a_pdf(transcribed):
    """The standing rule 'Summarize cost, timeline in weeks, and download links
    in your final Answer' turned every question into a quote."""
    prompt = render_system_prompt(transcribed, proposal_mode=False)

    assert "Summarize cost, timeline in weeks" not in prompt
    assert "Proposal Chaining Sequence" not in prompt
    assert "business-development" not in prompt


def test_the_proposal_prompt_still_chains(transcribed):
    prompt = render_system_prompt(transcribed, proposal_mode=True)

    assert "Proposal Chaining Sequence" in prompt
    assert "generate_proposal_pdf" in prompt


def test_the_non_proposal_prompt_still_offers_the_brief_tools(transcribed):
    """Non-proposal mode narrows the instructions, not the visitor's document."""
    prompt = render_system_prompt(transcribed, proposal_mode=False)

    assert agent_tools.READ_BRIEF_TOOL_NAME in prompt
    assert "id=a3f9c2" in prompt


# ── read_brief ──────────────────────────────────────────────────────────────


def test_read_brief_returns_the_documents_own_words(transcribed):
    out = agent_tools.read_brief("a3f9c2", transcribed)

    assert "customer loyalty mobile app" in out


def test_read_brief_says_so_when_it_truncates(tmp_path):
    path = tmp_path / "long.md"
    path.write_text("x" * (agent_tools.BRIEF_EXCERPT_CHARS + 500), encoding="utf-8")
    brief = [BriefRef("u1", "b.pdf", "sha", markdown_path=str(path), pages_total=90)]

    out = agent_tools.read_brief("u1", brief)

    assert "truncated" in out.lower()


@pytest.fixture
def long_brief(tmp_path):
    """A document whose interesting clause sits past the excerpt cap.

    Modelled on the live failure: a 9-page RFP, 21k characters, where the
    visitor asked about clause 2.10 at character ~7,900.
    """
    path = tmp_path / "rfp.md"
    path.write_text(
        "## Page 1\n2.0 Broad description of Project: a digital media campaign.\n"
        + "filler about the campaign. " * 400
        + "\n## Page 4\n2.10 The City reserves the right to negotiate with the "
        "selected proposer the exact terms and conditions of the contract.\n"
        + "more filler. " * 200,
        encoding="utf-8",
    )
    return [
        BriefRef("u1", "rfp.pdf", "sha", markdown_path=str(path), pages_total=9, pages_parsed=9)
    ]


def test_read_brief_finds_a_clause_past_the_excerpt_cap(long_brief):
    """The regression. Without a query, `read_brief` always returns the head of
    the file, so everything past BRIEF_EXCERPT_CHARS was unreachable *by
    construction*: the tool took only an upload_id, so the model had no way to
    ask for more, and the loop's repeat guard correctly blocked the identical
    second call. Measured live: a 21k-char RFP answered "point 2.10 is not
    mentioned in the available content" when 2.10 sat at character 7,863."""
    head = agent_tools.read_brief("u1", long_brief)
    assert "2.10 The City reserves" not in head  # the bug's precondition

    out = agent_tools.read_brief({"upload_id": "u1", "query": "2.10"}, long_brief)

    assert "2.10 The City reserves the right to negotiate" in out


def test_read_brief_query_says_it_showed_only_matches(long_brief):
    """An excerpt presented as the whole document is how "I only read part of
    it" becomes an unqualified summary — the same rule the head path follows."""
    out = agent_tools.read_brief({"upload_id": "u1", "query": "2.10"}, long_brief)

    assert "matching" in out.lower()


def test_read_brief_labels_an_excerpt_by_the_page_of_the_match(long_brief):
    """The window opens ~350 characters before the hit, so it straddles the
    `## Page 4` heading. Labelling by the window start would report the page-4
    clause as "Page 3" — a wrong page number attached to a real quote."""
    out = agent_tools.read_brief({"upload_id": "u1", "query": "2.10"}, long_brief)

    assert "Page 4" in out
    assert "Page 3" not in out


def test_read_brief_says_when_a_query_matches_nothing(long_brief):
    """A no-match must not silently fall back to the head of the document: the
    model would read it as "here is what you asked for" and answer from
    whatever page 1 happens to say."""
    out = agent_tools.read_brief(
        {"upload_id": "u1", "query": "zzz-not-in-this-document"}, long_brief
    )

    assert "no " in out.lower() and "zzz-not-in-this-document" in out


def test_read_brief_without_a_query_still_returns_the_head(transcribed):
    """The query is additive; the bare-id call the model already makes must keep
    working unchanged."""
    out = agent_tools.read_brief("a3f9c2", transcribed)

    assert "customer loyalty mobile app" in out


def test_read_brief_reports_pages_hop_one_could_not_read(tmp_path):
    """A brief where vision choked on 3 pages must not read like a clean one."""
    path = tmp_path / "t.md"
    path.write_text("some text", encoding="utf-8")
    brief = [
        BriefRef(
            "u1", "b.pdf", "sha", markdown_path=str(path),
            pages_total=12, pages_parsed=9, pages_failed=[4, 5, 6],
        )
    ]

    out = agent_tools.read_brief("u1", brief)

    assert "4, 5, 6" in out


def test_read_brief_is_offered_only_when_something_is_attached(transcribed):
    with_doc = {s.name for s in agent_tools.build_tool_specs(transcribed)}
    without = {s.name for s in agent_tools.build_tool_specs()}

    assert agent_tools.READ_BRIEF_TOOL_NAME in with_doc
    assert agent_tools.READ_BRIEF_TOOL_NAME not in without


def test_read_brief_never_reaches_an_unattached_document(tmp_path):
    """The id is model-typed free text; it may only select from this session's
    resolved uploads, never reach the filesystem."""
    outsider = tmp_path / "secret.md"
    outsider.write_text("classified", encoding="utf-8")

    with pytest.raises(ValueError):
        agent_tools.read_brief(str(outsider), [])
