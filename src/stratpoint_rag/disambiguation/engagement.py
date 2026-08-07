"""Ask the visitor for the client/project name. Never infer it.

``ExtractedRequirements`` has no ``client_name`` and no ``project_name``,
because a required field is an instruction to hallucinate one. But "the parser
must not invent them" is not the same as "the system can never know them" —
there is a third source neither docparse hop considered: **ask the human.**

Three sources, three different trust levels, and they must not be collapsed:

===================  ==========================  ==========================
Source               Trust                       Where it belongs
===================  ==========================  ==========================
The brief said it    document-derived,           a *suggestion* only
                     attacker-controllable       (``docparse.suggest_names``)
The visitor typed    human-confirmed             here, and
it                                               ``ProposalPDFInput``
The model guessed    **not permitted at all**    nowhere
===================  ==========================  ==========================

**A visitor-supplied name is never written back into
``ExtractedRequirements``.** That model is the parser's sworn statement about
what the *document* contained; merging a human-typed value into it would make
rows 1 and 2 indistinguishable, and it would do so invisibly, because the shape
would stay valid.

Four rules the design turns on:

1. **Ask late, not at extraction time.** The names have exactly one consumer,
   ``generate_proposal_pdf``. Asking at upload would tax every conversation with
   a round-trip most never need — *"what's the timeline on this?"* requires no
   client name at all. The trigger is a proposal request with the name still
   unknown.
2. **Ask once per session, and remember "no".** A declination is stored as an
   answer. Re-asking a visitor who already said "leave it blank" reads as
   broken. Keyed by session, not by ``sha256``: the answer is about the
   engagement, not about the file's bytes.
3. **The document may propose; only the visitor may confirm.** A transcript
   often states a client name and offering it is genuinely useful — but a brief
   is attacker-controllable text. The name is recorded only once the visitor
   affirms it. If they never engage it stays None: silence is not consent.
4. **``ClarificationLoop`` does the asking.** ``disambiguation`` already owns
   "clarify intent before tool calls"; a second asking mechanism would be a
   second thing to keep correct. Both slots are ``required=False`` so the loop
   terminates when the visitor declines.

This is the cheap version of the injection mitigation both docparse plans defer.
It costs one confirmation and removes the highest-value injection target in the
feature, because ``client_name`` is the field that ends up printed on a
commercial document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .clarification import ClarificationLoop
from .schemas import IntentCategory
from .slots import is_declination

__all__ = [
    "Engagement",
    "clear",
    "get",
    "needs_ask",
    "record_answer",
    "start_ask",
]

_SLOTS = ["brief_client_name", "brief_project_name"]

_AFFIRM = re.compile(
    r"^\s*(yes|yep|yeah|yup|sure|ok|okay|correct|right|that'?s right|"
    r"use (?:it|that|them)|go ahead|please do|sounds good)[\s.!]*$",
    re.IGNORECASE,
)


@dataclass
class Engagement:
    """What this session settled about naming the proposal."""

    client_name: str | None = None
    project_name: str | None = None
    # An explicit "leave them blank" — stored so it is never asked again.
    declined: bool = False
    asked: bool = False
    # The in-flight question, if the last turn was the ask.
    loop: ClarificationLoop | None = None
    # The request that triggered the ask, replayed once the ask is answered so
    # the visitor does not have to repeat themselves.
    pending_request: str | None = None
    # What the document claimed, offered for confirmation and nothing else.
    suggestion: tuple[str | None, str | None] = field(default=(None, None))

    @property
    def names(self) -> tuple[str | None, str | None]:
        return self.client_name, self.project_name

    @property
    def settled(self) -> bool:
        """True once the visitor has answered — with a name or with a refusal."""
        return self.declined or self.asked or any(self.names)


_sessions: dict[str, Engagement] = {}


def get(session_id: str | None) -> Engagement:
    sid = session_id or "default"
    if sid not in _sessions:
        _sessions[sid] = Engagement()
    return _sessions[sid]


def clear(session_id: str | None = None) -> None:
    """Drop one session's naming answer. Wired to 'Reset conversation'."""
    _sessions.pop(session_id or "default", None)


def needs_ask(session_id: str | None) -> bool:
    """True when a proposal is wanted and naming has not been settled."""
    engagement = get(session_id)
    return engagement.loop is None and not engagement.settled


def _question(suggestion: tuple[str | None, str | None]) -> str:
    """One question covering both slots.

    Combined rather than two sequential turns: this is a courtesy question in
    the middle of someone asking for a quote, and two round-trips to collect two
    optional fields would cost more goodwill than the fields are worth.

    A document-derived name is offered **attributed to the document**, so the
    visitor can see where it came from before agreeing to put it on a proposal.
    """
    client, project = suggestion
    if client and project:
        seen = f'The document mentions "{client}" and "{project}". '
        offer = "Shall I use those"
    elif client:
        seen = f'The document mentions "{client}". '
        offer = "Shall I use that as the client name"
    elif project:
        seen = f'The document mentions "{project}" as the project. '
        offer = "Shall I use that"
    else:
        return (
            "Before I put this together — is there a client name and project "
            "name you'd like on the proposal? Just say 'skip' to leave them "
            "blank; the proposal works either way."
        )
    return (
        f"{seen}{offer}, give me different names, or leave them blank? "
        "'Skip' is a perfectly good answer."
    )


def start_ask(
    session_id: str | None,
    request: str,
    suggestion: tuple[str | None, str | None] = (None, None),
) -> str:
    """Begin the naming ask. Returns the question to put to the visitor."""
    engagement = get(session_id)
    engagement.loop = ClarificationLoop(
        intent=IntentCategory.REQUEST_PROPOSAL,
        missing_slots=list(_SLOTS),
        max_turns=1,  # one question, then it is settled either way
    )
    engagement.pending_request = request
    engagement.suggestion = suggestion
    return _question(suggestion)


@dataclass(frozen=True)
class Resumption:
    """What to do after the visitor answered the naming question."""

    request: str
    names: tuple[str | None, str | None]
    declined: bool


def record_answer(session_id: str | None, answer: str) -> Resumption:
    """Consume the visitor's reply to the naming question.

    Always settles: a name, an affirmation of the document's suggestion, or a
    declination. There is no path that asks again, which is the point — both
    slots are optional and the proposal is producible without either.
    """
    engagement = get(session_id)
    loop = engagement.loop
    request = engagement.pending_request or answer

    engagement.asked = True
    engagement.loop = None
    engagement.pending_request = None

    if _AFFIRM.match(answer or ""):
        # Only here does a document-derived name become a usable value, and only
        # because the visitor said so.
        engagement.client_name, engagement.project_name = engagement.suggestion
    elif is_declination(answer):
        engagement.declined = True
    elif loop is not None:
        confirmed = loop.process_answer(answer).slots
        engagement.client_name = confirmed.get("brief_client_name")
        engagement.project_name = confirmed.get("brief_project_name")

    if not any(engagement.names):
        # Nothing usable came back. Treat it as a declination rather than
        # re-asking: the visitor answered, the answer just contained no name.
        engagement.declined = True

    return Resumption(
        request=request, names=engagement.names, declined=engagement.declined
    )
