"""Asking the visitor to name the proposal — and taking 'no' for an answer.

The rule the whole feature turns on: a visitor-supplied name never reaches
``ExtractedRequirements``. That model is the parser's statement about what the
*document* contained; a human-typed value merged into it would make
"the brief said it" and "the visitor typed it" indistinguishable, and the shape
would stay valid, so nothing would fail.
"""

import pytest

from stratpoint_rag.disambiguation import engagement
from stratpoint_rag.disambiguation.classifier import classify
from stratpoint_rag.disambiguation.router import route
from stratpoint_rag.disambiguation.schemas import IntentCategory
from stratpoint_rag.disambiguation.slots import (
    INTENT_SLOTS,
    extract_slots,
    is_declination,
)


@pytest.fixture(autouse=True)
def _clean_session():
    engagement.clear("s1")
    yield
    engagement.clear("s1")


# ── the new intent ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "can you put together a proposal?",
        "give me a quote",
        "how much would this cost?",
        "scope out my project please",
        "how long will this take?",
        "I need a statement of work",
    ],
)
def test_proposal_requests_are_classified_as_such(message):
    assert classify(message).intent == IntentCategory.REQUEST_PROPOSAL


@pytest.mark.parametrize(
    "message",
    [
        "Does Stratpoint use Flutter?",
        "What consulting services do you offer?",
        "Where are you located?",
    ],
)
def test_ordinary_questions_are_not_proposal_requests(message):
    """A false positive costs the visitor a naming question they did not need."""
    assert classify(message).intent != IntentCategory.REQUEST_PROPOSAL


def test_the_router_lets_a_proposal_request_through_without_clarifying():
    result = route("can you put together a proposal?")

    assert result.intent == IntentCategory.REQUEST_PROPOSAL
    assert result.should_retrieve
    assert result.clarification_question is None


# ── the slots ───────────────────────────────────────────────────────────────


def test_both_slots_are_optional():
    """A required slot loops until a value arrives, which is exactly the
    coercion this design set out to remove.

    Asserted as a property of every slot rather than of an exact count: the
    intent gained `target_timeline` later, and a count made an unrelated
    addition look like a regression in the naming design.
    """
    slots = INTENT_SLOTS[IntentCategory.REQUEST_PROPOSAL]

    assert {"brief_client_name", "brief_project_name"} <= {s.name for s in slots}
    assert all(not s.required for s in slots)


def test_the_slot_names_do_not_collide_with_the_stratpoint_ones():
    """INTENT_SLOTS[ASK_STRATPOINT] already has a `project_name`, and it means
    a Stratpoint case study — not the visitor's own project."""
    names = {s.name for s in INTENT_SLOTS[IntentCategory.REQUEST_PROPOSAL]}

    assert {"brief_client_name", "brief_project_name"} <= names
    assert "project_name" not in names


def test_stratpoint_topic_patterns_do_not_leak_into_a_proposal():
    """Otherwise 'the SM Retail project' stuffs a Stratpoint case study into
    someone else's proposal."""
    query = extract_slots("about the SM Retail project", IntentCategory.REQUEST_PROPOSAL)

    assert "project_name" not in query.slots
    assert "topic" not in query.slots


def test_a_labelled_answer_fills_both_slots():
    query = extract_slots(
        "client is Northwind Retail, project is Loyalty App",
        IntentCategory.REQUEST_PROPOSAL,
    )

    assert query.slots["brief_client_name"] == "Northwind Retail"
    assert query.slots["brief_project_name"] == "Loyalty App"


def test_a_bare_answer_lands_in_the_slot_that_was_asked():
    query = extract_slots(
        "Northwind Retail",
        IntentCategory.REQUEST_PROPOSAL,
        target_slot="brief_client_name",
    )

    assert query.slots == {"brief_client_name": "Northwind Retail"}


@pytest.mark.parametrize(
    "answer", ["", "   ", "no", "skip", "none", "n/a", "leave them blank", "no thanks"]
)
def test_declinations_are_recognised(answer):
    """Commit 7f9ae17 stopped empty input escalating to the LLM. A blank answer
    HERE is a deliberate 'leave them empty', not ambiguous input."""
    assert is_declination(answer)


@pytest.mark.parametrize("answer", ["Northwind Retail", "Nordic Systems"])
def test_a_real_name_is_not_a_declination(answer):
    assert not is_declination(answer)


# ── the ask ─────────────────────────────────────────────────────────────────


def test_a_fresh_session_needs_the_ask():
    assert engagement.needs_ask("s1")


def test_the_question_offers_leaving_them_blank():
    question = engagement.start_ask("s1", "give me a quote")

    assert "skip" in question.lower() or "blank" in question.lower()


def test_a_document_name_is_offered_as_a_suggestion_not_adopted():
    question = engagement.start_ask("s1", "quote", ("Northwind Retail", None))

    assert "Northwind Retail" in question
    assert "document" in question.lower()  # attributed, not asserted
    # Nothing recorded yet: the visitor has not answered.
    assert engagement.get("s1").names == (None, None)


def test_silence_is_not_consent():
    """The suggestion stays a suggestion until the visitor engages with it."""
    engagement.start_ask("s1", "quote", ("Northwind Retail", None))

    assert engagement.get("s1").client_name is None


# ── the answer ──────────────────────────────────────────────────────────────


def test_an_affirmation_records_the_documents_suggestion():
    engagement.start_ask("s1", "give me a quote", ("Northwind Retail", "Loyalty App"))

    resumed, prompt = engagement.record_answer("s1", "yes")

    assert resumed.names == ("Northwind Retail", "Loyalty App")
    assert not resumed.declined
    assert prompt is not None
    assert "Northwind Retail" in prompt


def test_a_typed_name_is_recorded():
    engagement.start_ask("s1", "give me a quote")

    resumed, prompt = engagement.record_answer("s1", "Nordic Systems")

    assert resumed.names[0] == "Nordic Systems"
    assert prompt is not None


def test_a_typed_name_overrides_the_documents_suggestion():
    engagement.start_ask("s1", "quote", ("Northwind Retail", None))

    resumed, prompt = engagement.record_answer("s1", "Nordic Systems")

    assert resumed.names[0] == "Nordic Systems"
    assert prompt is not None


def test_a_declination_is_recorded_as_an_answer():
    engagement.start_ask("s1", "give me a quote", ("Northwind Retail", None))

    resumed, prompt = engagement.record_answer("s1", "skip")

    assert resumed.declined
    assert resumed.names == (None, None)
    assert prompt is None


def test_the_original_request_is_replayed():
    """The visitor should not have to retype what they asked for."""
    engagement.start_ask("s1", "put together a proposal for me")

    resumed, _ = engagement.record_answer("s1", "skip")
    assert resumed.request == (
        "put together a proposal for me"
    )


def test_the_ask_happens_once_per_session_even_after_a_no():
    engagement.start_ask("s1", "quote")
    engagement.record_answer("s1", "skip")

    assert not engagement.needs_ask("s1")


def test_the_ask_happens_once_per_session_after_a_name():
    engagement.start_ask("s1", "quote")
    engagement.record_answer("s1", "Nordic Systems")

    assert not engagement.needs_ask("s1")


def test_the_loop_terminates_on_an_unusable_answer():
    """Both slots are optional; an answer with no name in it settles the matter
    rather than asking again."""
    engagement.start_ask("s1", "quote")
    engagement.record_answer("s1", "!!!")

    assert not engagement.needs_ask("s1")
    assert engagement.get("s1").declined


def test_sessions_are_independent():
    engagement.start_ask("s1", "quote")
    engagement.record_answer("s1", "Nordic Systems")
    try:
        assert engagement.needs_ask("s2")
    finally:
        engagement.clear("s2")


def test_clearing_a_session_forgets_the_answer():
    engagement.start_ask("s1", "quote")
    engagement.record_answer("s1", "skip")

    engagement.clear("s1")

    assert engagement.needs_ask("s1")


# ── the invariant ───────────────────────────────────────────────────────────


def test_no_path_writes_a_name_into_extracted_requirements():
    from stratpoint_rag.docparse.schema import ExtractedRequirements

    engagement.start_ask("s1", "quote", ("Northwind Retail", "Loyalty App"))
    engagement.record_answer("s1", "yes")

    assert "client_name" not in ExtractedRequirements.model_fields
    assert "project_name" not in ExtractedRequirements.model_fields


# ── confirmation & change command tests ─────────────────────────────────────


def test_affirmation_includes_proceed_and_looks_good():
    from stratpoint_rag.disambiguation.engagement import _AFFIRM

    for phrase in [
        "looks good",
        "proceed",
        "looks great",
        "all good",
        "confirm",
        "confirmed",
    ]:
        assert _AFFIRM.match(phrase), f"Failed to match affirmation: {phrase}"


def test_extract_proposal_names_change_commands():
    from stratpoint_rag.disambiguation.schemas import IntentCategory
    from stratpoint_rag.disambiguation.slots import extract_slots

    q1 = extract_slots("change client to Acme Corp", IntentCategory.REQUEST_PROPOSAL)
    assert q1.slots.get("brief_client_name") == "Acme Corp"

    q2 = extract_slots(
        "change project name to Alpha Initiative", IntentCategory.REQUEST_PROPOSAL
    )
    assert q2.slots.get("brief_project_name") == "Alpha Initiative"

    q3 = extract_slots(
        "Client Name is Clive and Project name is Project Avisala",
        IntentCategory.REQUEST_PROPOSAL,
    )
    assert q3.slots.get("brief_client_name") == "Clive"
    assert q3.slots.get("brief_project_name") == "Project Avisala"


def test_format_confirmation():
    from stratpoint_rag.disambiguation.engagement import format_confirmation

    msg = format_confirmation("Clive", "Project Avisala")
    assert "Client Name: Clive" in msg
    assert "Project Name: Project Avisala" in msg
    assert "Are these the right details or do you want to change them?" in msg

    msg_partial = format_confirmation("Clive", None)
    assert "Client Name: Clive" in msg_partial
    assert "Project Name: (Not specified)" in msg_partial


def test_start_confirmation_sets_state():
    prompt = engagement.start_confirmation(
        "s1", "give me a quote", "Clive", "Project Avisala"
    )
    assert "Client Name: Clive" in prompt
    assert engagement.get("s1").awaiting_confirmation
    assert engagement.get("s1").client_name == "Clive"
    assert engagement.get("s1").project_name == "Project Avisala"


def test_confirmation_response_affirmation():
    engagement.start_confirmation("s1", "give me a quote", "Clive", "Project Avisala")
    resumption, prompt = engagement.record_confirmation_response(
        "s1", "looks good, proceed"
    )

    assert prompt is None
    assert resumption.names == ("Clive", "Project Avisala")
    assert resumption.request == "give me a quote"
    assert engagement.get("s1").settled
    assert not engagement.get("s1").awaiting_confirmation


def test_confirmation_response_correction():
    engagement.start_confirmation("s1", "give me a quote", "Clive", "Project Avisala")
    resumption, prompt = engagement.record_confirmation_response(
        "s1", "change client to Monica"
    )

    assert prompt is not None
    assert "Client Name: Monica" in prompt
    assert "Project Name: Project Avisala" in prompt
    assert engagement.get("s1").client_name == "Monica"
    assert engagement.get("s1").awaiting_confirmation


def test_confirmation_response_declination():
    engagement.start_confirmation("s1", "give me a quote", "Clive", "Project Avisala")
    resumption, prompt = engagement.record_confirmation_response("s1", "skip")

    assert prompt is None
    assert resumption.names == (None, None)
    assert resumption.declined
    assert engagement.get("s1").settled


# ── an unparsed reply re-asks; it is not a declination ──────────────────────
#
# The confirmation question is "are these right or do you want to change them?".
# Three replies a visitor plainly means as a correction were all landing in the
# catch-all that recorded a declination and wiped both names, so the quote
# printed the placeholder immediately after they supplied the real name.


def test_a_bare_name_at_confirmation_reasks_instead_of_declining():
    """"Northwind Retail" alone carries no slot label, so nothing extracts.

    That is ambiguous, not a refusal — ask again rather than settling on a
    silent wipe of names the visitor never withdrew.
    """
    engagement.start_confirmation("s1", "give me a quote", "Clive", "Project Avisala")
    resumption, prompt = engagement.record_confirmation_response("s1", "Northwind Retail")

    assert prompt is not None, "an unparsed reply must re-ask"
    assert not resumption.declined
    assert engagement.get("s1").names == ("Clive", "Project Avisala")
    assert engagement.get("s1").awaiting_confirmation
    assert not engagement.get("s1").settled


def test_no_at_confirmation_reasks_instead_of_wiping_names():
    """"No" answers "are these right?" — it does not mean "use no names"."""
    engagement.start_confirmation("s1", "give me a quote", "Clive", "Project Avisala")
    resumption, prompt = engagement.record_confirmation_response("s1", "no")

    assert prompt is not None
    assert not resumption.declined
    assert engagement.get("s1").names == ("Clive", "Project Avisala")
    assert engagement.get("s1").awaiting_confirmation


def test_an_actually_prefixed_correction_is_applied():
    """`_NOT_A_NAME` rejects a leading "actually" before the slot regexes run.

    The regexes carry an explicit ``actually\\s+`` prefix for exactly this
    phrasing, so a labelled correction must reach them.
    """
    engagement.start_confirmation("s1", "give me a quote", "Clive", "Project Avisala")
    resumption, prompt = engagement.record_confirmation_response(
        "s1", "actually the client is Monica"
    )

    assert prompt is not None
    assert "Client Name: Monica" in prompt
    assert engagement.get("s1").client_name == "Monica"
    assert not resumption.declined


# ── a declination actually withdraws the names ──────────────────────────────


def test_a_declination_clears_the_stored_names():
    """`record_answer` returned (None, None) but left the names in the session.

    The caller reads ``engagement.get(sid).names``, not the Resumption's, so a
    name supplied earlier printed on the quote after an explicit skip.
    """
    engagement.adopt_stated("s1", {"brief_client_name": "Acme"})
    engagement.start_ask("s1", "give me a quote")

    resumption, prompt = engagement.record_answer("s1", "skip")

    assert prompt is None
    assert resumption.declined
    assert resumption.names == (None, None)
    assert engagement.get("s1").names == (None, None), (
        "the stored names are what reaches the quote"
    )


def test_restating_one_name_keeps_the_other():
    """`start_confirmation` wrote `None` over an already-confirmed name.

    Reached whenever a follow-up request mentions only one of the two names —
    `route_result.slots` then carries a single key and the other arrives None.
    """
    engagement.start_confirmation("s1", "give me a quote", "Acme", "Loyalty App")
    engagement.record_confirmation_response("s1", "yes")

    prompt = engagement.start_confirmation(
        "s1", "actually call the project Rewards App", None, "Rewards App"
    )

    assert "Project Name: Rewards App" in prompt
    assert "Client Name: Acme" in prompt
    assert engagement.get("s1").client_name == "Acme"


