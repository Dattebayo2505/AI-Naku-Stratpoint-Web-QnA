"""A loop that stalls over an attachment must not answer from the website.

Regression suite for a live transcript. The visitor uploaded
``digital-advertising-scope.pdf`` (a 9-page SCMPD police-recruitment RFP) and
asked *"What is the current document context digital-advertising-scope?"*. They
were told the document was about "digital advertising ... with examples of
projects and services provided by Stratpoint, such as Automated Article Tagging
for Summit Media", cited to two stratpoint.com pages. Nothing in that answer
came from their file.

Two defects chained, and either one alone is enough to reproduce it:

1. The model picked ``read_brief`` correctly and then emitted a **byte-identical
   Thought/Action turn six times running**. Nothing in the loop noticed: the
   same 6 KB observation was re-executed and re-appended each turn, so the state
   the model conditioned on never changed and it never transitioned to Answer.
2. On exhausting ``MAX_TURNS`` the loop fell back to ``search_stratpoint``
   unconditionally — the website corpus — even though a document was attached
   and every step so far had been about it. That is where the RAG citations came
   from, and it is the more dangerous of the two: it substitutes a confident
   answer about the wrong subject and attaches sources that make it look
   verified.

The two sections below correspond, in order.
"""
import pytest

from stratpoint_rag.agent import react, tools
from stratpoint_rag.docparse import BriefRef

DOC_TEXT = (
    "Contents of 'digital-advertising-scope.pdf' (9 of 9 pages readable):\n\n"
    "## Page 1\nSCOPE OF WORK. Digital Advertising Campaign for Recruiting "
    "Officers for the Savannah-Chatham Metropolitan Police Department."
)

READ_BRIEF_ACTION = "Thought: what the document is about\nAction: read_brief(u1)"


class ScriptedChat:
    """Returns canned assistant turns in order and records what it was sent."""

    def __init__(self, *turns):
        self._turns = list(turns)
        self.calls = []

    def __call__(self, messages, stop):
        self.calls.append({"messages": [dict(m) for m in messages], "stop": list(stop)})
        if not self._turns:
            raise AssertionError("chat called more times than the script allows")
        return self._turns.pop(0)


@pytest.fixture
def brief(tmp_path):
    path = tmp_path / "transcription.md"
    path.write_text(DOC_TEXT, encoding="utf-8")
    return [
        BriefRef(
            upload_id="u1",
            filename="digital-advertising-scope.pdf",
            sha256="sha",
            markdown_path=str(path),
            pages_total=9,
            pages_parsed=9,
        )
    ]


@pytest.fixture
def website(monkeypatch):
    """Records every website-corpus call. It must stay empty on the doc path."""
    calls = []
    monkeypatch.setattr(
        tools,
        "search_stratpoint",
        lambda q: calls.append(q)
        or "We do digital advertising.\n\nSources used:\n"
        "- Summit Media (https://stratpoint.com/portfolio-summitmedia/)",
    )
    return calls


# ── 1. the stall ────────────────────────────────────────────────────────────


def test_an_identical_repeated_action_is_not_re_executed(brief, monkeypatch):
    """Six identical calls returned six identical 6 KB observations. Re-running
    the tool cannot change the answer; it only grows the context."""
    ran = []
    monkeypatch.setattr(
        tools, "read_brief", lambda raw, briefs=None: ran.append(raw) or DOC_TEXT
    )

    chat = ScriptedChat(READ_BRIEF_ACTION, READ_BRIEF_ACTION, "Answer: It is an RFP.")
    r = react.run_react("what is this doc?", chat=chat, briefs=brief)

    assert r.answer == "It is an RFP."
    assert len(ran) == 1


def test_the_repeat_is_told_to_answer_rather_than_handed_the_text_again(brief):
    chat = ScriptedChat(READ_BRIEF_ACTION, READ_BRIEF_ACTION, "Answer: An RFP.")
    r = react.run_react("what is this doc?", chat=chat, briefs=brief)

    second = [s for s in r.trace if s.type == "observation"][1].content
    assert "already called" in second.lower()
    assert "answer" in second.lower()
    assert "SCOPE OF WORK" not in second  # not the document a second time


@pytest.fixture
def truncated_brief(tmp_path):
    """A document long enough that a bare read comes back partial."""
    path = tmp_path / "long.md"
    path.write_text(
        "## Page 1\n" + "opening filler. " * 500
        + "\n## Page 9\nThe submission deadline is September 15, 2026.\n",
        encoding="utf-8",
    )
    return [
        BriefRef("u1", "rfp.pdf", "sha", markdown_path=str(path),
                 pages_total=9, pages_parsed=9)
    ]


def test_a_repeat_over_a_partial_excerpt_offers_the_search_back(truncated_brief):
    """"Answer now" is the wrong instruction when only part of the document came
    back. The plain nudge mentioned neither the truncation nor the search, so a
    stalled loop produced a confident, unqualified summary of a document it had
    read a third of — reported live against an 18-page deck summarized from its
    first 8 pages."""
    chat = ScriptedChat(READ_BRIEF_ACTION, READ_BRIEF_ACTION, "Answer: An RFP.")
    r = react.run_react("what is this doc?", chat=chat, briefs=truncated_brief,
                        proposal_mode=False)

    second = [s for s in r.trace if s.type == "observation"][1].content
    assert "only the first part" in second.lower()
    assert "query" in second  # the escape hatch, named concretely
    assert "u1" in second     # ...with the id it needs, not a placeholder
    assert "only part of the document" in second.lower()  # caveat if it answers


def test_a_repeat_over_a_complete_read_still_just_says_answer(brief):
    """The tool-aware branch must not blunt the plain nudge where it was right:
    with the whole document in hand there is nothing further to search for, and
    inviting a search would reopen the stall the guard exists to close."""
    chat = ScriptedChat(READ_BRIEF_ACTION, READ_BRIEF_ACTION, "Answer: An RFP.")
    r = react.run_react("what is this doc?", chat=chat, briefs=brief)

    second = [s for s in r.trace if s.type == "observation"][1].content
    assert "only the first part" not in second.lower()
    assert "answer" in second.lower()


def test_the_truncated_nudge_is_keyed_to_the_marker_read_brief_writes(
    truncated_brief,
):
    """Read off the prior observation, never re-derived from the excerpt cap —
    so the stamp and the matcher cannot drift apart."""
    head = tools.read_brief("u1", truncated_brief)

    assert tools.TRUNCATION_MARKER in head
    assert react.TRUNCATION_MARKER is tools.TRUNCATION_MARKER


def test_a_repeat_with_a_different_input_still_runs(brief, website):
    """The guard keys on (tool, input). Two genuinely different searches are
    two searches, not a stall."""
    chat = ScriptedChat(
        "Action: search_stratpoint(cloud)",
        "Action: search_stratpoint(flutter)",
        "Answer: Both.",
    )
    react.run_react("cloud and flutter?", chat=chat, briefs=brief)

    assert website == ["cloud", "flutter"]


# ── 2. the fallback must not leave the document ─────────────────────────────


def test_a_stalled_attachment_loop_never_answers_from_the_website(brief, website):
    """The reported transcript. Every turn was about their file; the answer came
    back about Summit Media."""
    chat = ScriptedChat(
        *[READ_BRIEF_ACTION] * react.MAX_TURNS,
        "The document is a police-recruitment RFP for SCMPD.",  # fallback summary
    )
    r = react.run_react("what is this doc?", chat=chat, briefs=brief)

    assert website == []
    assert r.citations == []
    assert "SCMPD" in r.answer


def test_the_fallback_summarizes_the_document_it_already_read(brief):
    """It must reuse the observation already in the trace, not re-read or guess."""
    chat = ScriptedChat(
        *[READ_BRIEF_ACTION] * react.MAX_TURNS,
        "A recruitment RFP.",
    )
    react.run_react("what is this doc?", chat=chat, briefs=brief)

    final = chat.calls[-1]["messages"]
    assert "SCOPE OF WORK" in final[0]["content"]
    assert final[-1]["content"] == "what is this doc?"


def test_a_malformed_loop_with_an_attachment_also_stays_on_the_document(
    brief, website
):
    """The other route into the fallback: two unparseable turns."""
    chat = ScriptedChat("Just prose.", "Still prose.", "It is an RFP for SCMPD.")
    r = react.run_react("what is this doc?", chat=chat, briefs=brief)

    assert website == []
    assert "SCMPD" in r.answer


def test_the_website_fallback_is_unchanged_when_nothing_is_attached(website):
    """Narrowing the fallback must not blunt it where it was always right."""
    chat = ScriptedChat(*["Action: search_stratpoint(x)"] * react.MAX_TURNS)
    r = react.run_react("what do you do?", chat=chat)

    assert website[-1] == "what do you do?"
    assert {c.url for c in r.citations} == {
        "https://stratpoint.com/portfolio-summitmedia/"
    }
