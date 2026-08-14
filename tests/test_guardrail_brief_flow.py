"""How an attachment and a proposal request move through run_with_guardrails.

Two routing facts this file pins down:

- an attachment forces the ReAct path. Sending "what's the timeline on this?"
  to answer_grounded() would search the *website* corpus and answer confidently
  about the wrong thing, because only the loop can reach the brief;
- the naming answer is consumed before routing. "Northwind Retail" classified on
  its own is a vague fragment the router would bounce straight back to
  clarification — but it is an answer to a question we asked.
"""

import pytest

from stratpoint_rag.agent import guardrail_agent as ga
from stratpoint_rag.agent.models import AgentResult
from stratpoint_rag.disambiguation import classifier, engagement
from stratpoint_rag.docparse import BriefRef

SESSION = "brief-flow"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    ga.clear_memory(SESSION)
    # Keep every test offline: no guardrail LLM calls, no NeMo import attempt.
    monkeypatch.setattr(ga, "_run_output_guardrails", lambda t, c, cfg, n: (t, None))
    monkeypatch.setattr(classifier, "_llm_classify", lambda text: None)
    yield
    ga.clear_memory(SESSION)



@pytest.fixture
def transcribed(tmp_path):
    path = tmp_path / "transcription.md"
    path.write_text(
        "## Page 1\nClient: Northwind Retail\nProject: Loyalty App\n", encoding="utf-8"
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


# ── an attachment routes through the loop ───────────────────────────────────


def test_an_attachment_forces_the_agent_path(monkeypatch, transcribed):
    seen = _capture_run_agent(monkeypatch)

    ga.run_with_guardrails(
        "what's the timeline on this?", session_id=SESSION, use_nemo=False,
        briefs=transcribed,
    )

    assert seen["briefs"] == transcribed


def test_without_an_attachment_an_ordinary_question_still_uses_rag(monkeypatch):
    """Regression guard: forcing everything through the loop would drop the
    grounded-answer path for the site corpus."""
    called = []
    monkeypatch.setattr(ga, "run_agent", lambda *a, **k: called.append(1))
    monkeypatch.setattr(
        ga, "answer_grounded", lambda q, k=8, enable_reasoning=False: ("a", [], None, None)
    )

    ga.run_with_guardrails(
        "Does Stratpoint use Flutter?", session_id=SESSION, use_nemo=False
    )

    assert not called


# ── the naming ask ──────────────────────────────────────────────────────────


def test_a_proposal_request_asks_how_to_name_it_first(monkeypatch, transcribed):
    monkeypatch.setattr(ga, "run_agent", lambda *a, **k: AgentResult(answer="quoted"))

    result = ga.run_with_guardrails(
        "can you put together a proposal?", session_id=SESSION, use_nemo=False,
        briefs=transcribed,
    )

    assert "Northwind Retail" in result.answer
    assert engagement.get(SESSION).loop is not None


def test_names_stated_in_the_request_skip_the_ask(monkeypatch, transcribed):
    """Asking for what the visitor just typed reads as not having listened.

    Upfront names trigger confirmation instead of asking for names again.
    Upon user confirmation, proposal generation proceeds with the stated names.
    """
    seen = _capture_run_agent(monkeypatch)

    result = ga.run_with_guardrails(
        "Generate me a default proposal for this document, project name is "
        "Savannah-OohLala, client name is Monica.",
        session_id=SESSION,
        use_nemo=False,
        briefs=transcribed,
    )

    assert result.guardrail_reason == "Awaiting proposal details confirmation"
    assert "Confirming the following details:" in result.answer
    assert "Client Name: Monica" in result.answer
    assert "Project Name: Savannah-OohLala" in result.answer
    assert engagement.get(SESSION).awaiting_confirmation

    ga.run_with_guardrails(
        "yes", session_id=SESSION, use_nemo=False, briefs=transcribed
    )
    assert seen["names"] == ("Monica", "Savannah-OohLala")
    assert engagement.get(SESSION).loop is None


def test_one_stated_name_is_enough_to_settle_the_naming(monkeypatch, transcribed):
    seen = _capture_run_agent(monkeypatch)

    result = ga.run_with_guardrails(
        "quote this please, client is Nordic Systems",
        session_id=SESSION,
        use_nemo=False,
        briefs=transcribed,
    )

    assert result.guardrail_reason == "Awaiting proposal details confirmation"
    assert "Client Name: Nordic Systems" in result.answer
    assert "Project Name: (Not specified)" in result.answer

    ga.run_with_guardrails(
        "yes", session_id=SESSION, use_nemo=False, briefs=transcribed
    )
    assert seen["names"] == ("Nordic Systems", None)


def test_a_stated_name_overrides_an_earlier_declination(monkeypatch, transcribed):
    """'skip' settles the question; it does not make the visitor unable to
    change their mind later by simply saying the name."""
    seen = _capture_run_agent(monkeypatch)

    ga.run_with_guardrails(
        "give me a quote", session_id=SESSION, use_nemo=False, briefs=transcribed
    )
    ga.run_with_guardrails(
        "skip", session_id=SESSION, use_nemo=False, briefs=transcribed
    )
    r3 = ga.run_with_guardrails(
        "redo the quote, client name is Monica",
        session_id=SESSION,
        use_nemo=False,
        briefs=transcribed,
    )
    assert "Confirming the following details:" in r3.answer
    assert "Client Name: Monica" in r3.answer

    ga.run_with_guardrails(
        "yes", session_id=SESSION, use_nemo=False, briefs=transcribed
    )
    assert seen["names"] == ("Monica", None)


def test_an_unlabelled_proposal_request_is_never_read_as_a_name(
    monkeypatch, transcribed
):
    """The whole sentence must not become the client name — that is the failure
    the ask exists to avoid, and adopting names from the request re-opens it."""
    monkeypatch.setattr(ga, "run_agent", lambda *a, **k: AgentResult(answer="quoted"))

    result = ga.run_with_guardrails(
        "can you put together a proposal?",
        session_id=SESSION,
        use_nemo=False,
        briefs=transcribed,
    )

    assert result.guardrail_reason == "Asked how to name the proposal"
    assert engagement.get(SESSION).names == (None, None)


def test_the_ask_does_not_count_as_the_bot_failing_to_understand(
    monkeypatch, transcribed
):
    """It is a question we chose to ask, not persistent vagueness — pushing it
    onto clarify_streak would march the visitor toward the hand-off message."""
    monkeypatch.setattr(ga, "run_agent", lambda *a, **k: AgentResult(answer="quoted"))

    ga.run_with_guardrails(
        "give me a quote", session_id=SESSION, use_nemo=False, briefs=transcribed
    )

    assert ga._get_memory(SESSION).clarify_streak == 0


def test_the_answer_is_recorded_and_the_request_replayed(monkeypatch, transcribed):
    seen = _capture_run_agent(monkeypatch)

    ga.run_with_guardrails(
        "put together a proposal",
        session_id=SESSION,
        use_nemo=False,
        briefs=transcribed,
    )
    r2 = ga.run_with_guardrails(
        "yes", session_id=SESSION, use_nemo=False, briefs=transcribed
    )
    assert "Confirming the following details:" in r2.answer
    assert "Northwind Retail" in r2.answer

    ga.run_with_guardrails(
        "yes", session_id=SESSION, use_nemo=False, briefs=transcribed
    )
    assert seen["message"] == "put together a proposal"
    assert seen["names"] == ("Northwind Retail", "Loyalty App")


def test_a_declination_still_produces_the_proposal(monkeypatch, transcribed):
    seen = _capture_run_agent(monkeypatch)

    ga.run_with_guardrails(
        "give me a quote", session_id=SESSION, use_nemo=False, briefs=transcribed
    )
    ga.run_with_guardrails(
        "skip", session_id=SESSION, use_nemo=False, briefs=transcribed
    )

    assert seen["message"] == "give me a quote"
    assert seen["names"] == (None, None)


def test_the_second_proposal_request_does_not_re_ask(monkeypatch, transcribed):
    """A visitor who already said 'leave it blank' has answered. Asking again
    reads as broken."""
    seen = _capture_run_agent(monkeypatch)

    ga.run_with_guardrails(
        "give me a quote", session_id=SESSION, use_nemo=False, briefs=transcribed
    )
    ga.run_with_guardrails(
        "skip", session_id=SESSION, use_nemo=False, briefs=transcribed
    )
    ga.run_with_guardrails(
        "give me a quote for the second phase too",
        session_id=SESSION, use_nemo=False, briefs=transcribed,
    )

    assert seen["message"] == "give me a quote for the second phase too"
    assert engagement.get(SESSION).loop is None  # no second ask


def test_resetting_the_conversation_forgets_the_naming_answer():
    engagement.start_ask(SESSION, "quote")
    engagement.record_answer(SESSION, "skip")

    ga.clear_memory(SESSION)

    assert engagement.needs_ask(SESSION)


# ── the suggestion comes from the document, not the model ───────────────────


def test_the_suggestion_is_read_from_the_transcription(transcribed):
    assert ga._name_suggestion(transcribed) == ("Northwind Retail", "Loyalty App")


def test_a_missing_transcription_file_suggests_nothing():
    gone = [BriefRef("u1", "b.pdf", "sha", markdown_path="/no/such/file.md")]

    assert ga._name_suggestion(gone) == (None, None)


def test_an_untranscribed_brief_is_skipped():
    assert ga._name_suggestion([BriefRef("u1", "b.pdf", "sha")]) == (None, None)


# ── confirmation flow tests ─────────────────────────────────────────────────


def test_initial_request_with_names_triggers_confirmation(monkeypatch, transcribed):
    seen = _capture_run_agent(monkeypatch)

    result = ga.run_with_guardrails(
        "Generate proposal, client name is Monica and project name is Loyalty App",
        session_id=SESSION,
        use_nemo=False,
        briefs=transcribed,
    )

    assert "Confirming the following details:" in result.answer
    assert "Client Name: Monica" in result.answer
    assert "Project Name: Loyalty App" in result.answer
    assert engagement.get(SESSION).awaiting_confirmation


def test_full_confirmation_flow_multi_turn(monkeypatch, transcribed):
    seen = _capture_run_agent(monkeypatch)

    # Turn 1: user asks for proposal
    r1 = ga.run_with_guardrails(
        "put together a proposal",
        session_id=SESSION,
        use_nemo=False,
        briefs=transcribed,
    )
    assert "Northwind Retail" in r1.answer

    # Turn 2: user provides names
    r2 = ga.run_with_guardrails(
        "Client Name is Clive and Project name is Project Avisala",
        session_id=SESSION,
        use_nemo=False,
        briefs=transcribed,
    )
    assert "Confirming the following details:" in r2.answer
    assert "Client Name: Clive" in r2.answer
    assert "Project Name: Project Avisala" in r2.answer

    # Turn 3: user confirms
    r3 = ga.run_with_guardrails(
        "looks good", session_id=SESSION, use_nemo=False, briefs=transcribed
    )
    assert seen["message"] == "put together a proposal"
    assert seen["names"] == ("Clive", "Project Avisala")


# ── irrelevant document guardrail tests ─────────────────────────────────────


def test_attached_resume_is_blocked_with_polite_refusal(monkeypatch, tmp_path):
    resume_path = tmp_path / "resume_transcription.md"
    resume_path.write_text(
        "## Page 1\n# Jane Doe\nEmail: jane@example.com\n\n"
        "## Education\nBS Computer Science, GPA 3.8\n"
        "## Work Experience\nFrontend Developer at Startup Co (2022-2024)\n"
        "## Skills\nJavaScript, React, CSS\n",
        encoding="utf-8",
    )
    brief = BriefRef(
        upload_id="resume123",
        filename="Jane_Doe_Resume.pdf",
        sha256="sha_resume",
        markdown_path=str(resume_path),
        pages_total=1,
        pages_parsed=1,
    )

    result = ga.run_with_guardrails(
        "Can you create a proposal and estimate for this?",
        session_id=SESSION,
        use_nemo=False,
        briefs=[brief],
    )

    assert "resume" in result.answer.lower()
    assert "project brief" in result.answer.lower()
    assert result.guardrail_reason is not None
    assert "Irrelevant document" in result.guardrail_reason
    assert not engagement.get(SESSION).awaiting_confirmation
    assert engagement.get(SESSION).loop is None


def test_attached_math_homework_is_blocked_on_generic_query(monkeypatch, tmp_path):
    math_path = tmp_path / "math_transcription.md"
    math_path.write_text(
        "## Page 1\n# Math 101: Problem Set 2\n"
        "Student: Bob\n\n"
        "Problem 1: Solve for x in 2x + 4 = 10. Show your work.\n"
        "Problem 2: Find the derivative of y = x^2.\n",
        encoding="utf-8",
    )
    brief = BriefRef(
        upload_id="math123",
        filename="homework2.pdf",
        sha256="sha_math",
        markdown_path=str(math_path),
        pages_total=1,
        pages_parsed=1,
    )

    result = ga.run_with_guardrails(
        "What is this document about?",
        session_id=SESSION,
        use_nemo=False,
        briefs=[brief],
    )

    assert "assignment" in result.answer.lower() or "homework" in result.answer.lower() or "math" in result.answer.lower()
    assert "project brief" in result.answer.lower()
    assert result.guardrail_reason is not None
    assert "Irrelevant document" in result.guardrail_reason


