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
    "Resumption",
    "abandon",
    "adopt_stated",
    "clear",
    "format_confirmation",
    "get",
    "needs_ask",
    "record_answer",
    "record_confirmation_response",
    "start_ask",
    "start_confirmation",
]

_SLOTS = ["brief_client_name", "brief_project_name"]

_AFFIRM = re.compile(
    r"^\s*(?:yes|yep|yeah|yup|sure|ok|okay|correct|right|that'?s right|"
    r"use (?:it|that|them)|go ahead|please do|sounds good|proceed|looks good|"
    r"looks great|all good|confirm|confirmed)(?:[\s,]+(?:and\s+)?(?:yes|yep|yeah|yup|sure|ok|okay|correct|right|that'?s right|use (?:it|that|them)|go ahead|please do|sounds good|proceed|looks good|looks great|all good|confirm|confirmed))*[\s.!]*$",
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
    awaiting_confirmation: bool = False
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
        return self.declined or (self.asked and not self.awaiting_confirmation)


_sessions: dict[str, Engagement] = {}


def get(session_id: str | None) -> Engagement:
    sid = session_id or "default"
    if sid not in _sessions:
        _sessions[sid] = Engagement()
    return _sessions[sid]


def clear(session_id: str | None = None) -> None:
    """Drop one session's naming answer. Wired to 'Reset conversation'."""
    _sessions.pop(session_id or "default", None)


def adopt_stated(session_id: str | None, slots: dict[str, str] | None) -> bool:
    """Record names the visitor already stated in the request itself."""
    client = (slots or {}).get("brief_client_name")
    project = (slots or {}).get("brief_project_name")
    if not (client or project):
        return False

    engagement = get(session_id)
    if client:
        engagement.client_name = client
    if project:
        engagement.project_name = project
    engagement.declined = False
    return True


def needs_ask(session_id: str | None) -> bool:
    """True when a proposal is wanted and naming has not been settled."""
    engagement = get(session_id)
    return engagement.loop is None and not engagement.awaiting_confirmation and not engagement.settled


def format_confirmation(client_name: str | None, project_name: str | None) -> str:
    c = client_name or "(Not specified)"
    p = project_name or "(Not specified)"
    return (
        "Confirming the following details:\n"
        f"Client Name: {c}\n"
        f"Project Name: {p}\n\n"
        "Are these the right details or do you want to change them?"
    )


def start_confirmation(
    session_id: str | None,
    request: str,
    client_name: str | None = None,
    project_name: str | None = None,
) -> str:
    engagement = get(session_id)
    # Only overwrite what was actually supplied — the same guard `adopt_stated`
    # carries. A follow-up naming just one of the two arrives with the other as
    # None, and assigning it would silently drop a name already confirmed.
    if client_name:
        engagement.client_name = client_name
    if project_name:
        engagement.project_name = project_name
    engagement.awaiting_confirmation = True
    engagement.pending_request = request
    engagement.loop = None
    # Render the merged state, not the arguments — otherwise the prompt shows
    # "(Not specified)" for a name this call deliberately left standing.
    return format_confirmation(engagement.client_name, engagement.project_name)


def _question(suggestion: tuple[str | None, str | None]) -> str:
    """One question covering both slots."""
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
    # False when the reply was not an answer at all. The caller must then route
    # the visitor's message as a fresh turn instead of replaying `request`.
    consumed: bool = True


_NOT_A_NAME = re.compile(
    r"^\s*(?:what|which|who|where|when|why|how|whats|what'?s|can|could|would|"
    r"should|do|does|did|is|are|tell|give|show|list|explain|describe|"
    r"summari[sz]e|wait|actually|no wait|hold on|first|instead)\b",
    re.IGNORECASE,
)

_MAX_NAME_CHARS = 80

# The subset of `is_declination` that means "these are wrong" rather than
# "leave the names off". Only meaningful facing the confirmation question;
# the naming *ask* asks something else, where a bare "no" is a real refusal.
_PLAIN_NO = re.compile(r"^\s*(?:no|nope|nah)[\s.!]*$", re.IGNORECASE)

_RECONFIRM_NUDGE = (
    "Sorry — I didn't catch which name that was. You can say "
    "\"client is X\" or \"project is Y\", 'yes' to use the details below, "
    "or 'skip' to leave them blank.\n\n"
)


def _is_an_answer(reply: str) -> bool:
    """True when the reply could plausibly be settling the naming question."""
    text = (reply or "").strip()
    if not text:  # silence is a declination, which IS an answer
        return True
    if _AFFIRM.match(text) or is_declination(text):
        return True
    if "?" in text:
        return False
    if len(text) > _MAX_NAME_CHARS:
        return False
    return not _NOT_A_NAME.match(text)


def abandon(session_id: str | None) -> None:
    """Drop an unanswered ask without recording an answer."""
    engagement = get(session_id)
    engagement.loop = None
    engagement.pending_request = None
    engagement.awaiting_confirmation = False


def record_confirmation_response(
    session_id: str | None,
    answer: str,
) -> tuple[Resumption, str | None]:
    """Process user response when awaiting confirmation.

    Returns (Resumption, next_prompt).
    If next_prompt is not None, the bot should immediately display next_prompt.
    """
    engagement = get(session_id)
    request = engagement.pending_request or answer

    # Extraction runs FIRST, ahead of the _is_an_answer heuristic. A labelled
    # correction is unambiguous, and _NOT_A_NAME rejects a leading "actually" —
    # the exact opener the slot regexes carry an `actually\s+` prefix for, which
    # made that prefix unreachable from this path.
    from .schemas import IntentCategory
    from .slots import extract_slots

    extracted = extract_slots(answer, IntentCategory.REQUEST_PROPOSAL).slots
    if extracted:
        if "brief_client_name" in extracted:
            engagement.client_name = extracted["brief_client_name"]
        if "brief_project_name" in extracted:
            engagement.project_name = extracted["brief_project_name"]
        return Resumption(
            request=request, names=engagement.names, declined=False
        ), format_confirmation(engagement.client_name, engagement.project_name)

    if _AFFIRM.match(answer or ""):
        engagement.awaiting_confirmation = False
        engagement.asked = True
        return Resumption(request=request, names=engagement.names, declined=False), None

    # "skip"/"blank"/"none" withdraw the names; a bare "no" answers the question
    # that was actually asked ("are these right?") and means the opposite of a
    # withdrawal, so it falls through to the re-ask below.
    if is_declination(answer) and not _PLAIN_NO.match(answer or ""):
        engagement.declined = True
        engagement.client_name = None
        engagement.project_name = None
        engagement.awaiting_confirmation = False
        engagement.asked = True
        return Resumption(request=request, names=(None, None), declined=True), None

    if not _is_an_answer(answer):
        abandon(session_id)
        return Resumption(
            request=answer, names=engagement.names, declined=False, consumed=False
        ), None

    # Nothing parsed. Ask again rather than recording a declination: the visitor
    # is mid-correction and has withdrawn nothing, so wiping both names here
    # printed the placeholder on a quote the moment they supplied a real name.
    # The nudge differs from the plain confirmation so a repeated reply cannot
    # loop on the identical prompt.
    return Resumption(
        request=request, names=engagement.names, declined=False
    ), _RECONFIRM_NUDGE + format_confirmation(
        engagement.client_name, engagement.project_name
    )


def record_answer(session_id: str | None, answer: str) -> tuple[Resumption, str | None]:
    """Consume the visitor's reply to the naming question.

    Settles on a name, an affirmation of the document's suggestion, or a
    declination — but only when the reply is *addressing the question*. A reply
    that plainly is not (a question of their own, a correction) abandons the ask
    and is handed back untouched via ``consumed=False``.
    """
    engagement = get(session_id)
    loop = engagement.loop
    request = engagement.pending_request or answer

    if not _is_an_answer(answer):
        abandon(session_id)
        return Resumption(
            request=answer, names=engagement.names, declined=False, consumed=False
        ), None

    engagement.loop = None
    engagement.pending_request = None

    if _AFFIRM.match(answer or ""):
        engagement.client_name, engagement.project_name = engagement.suggestion
    elif is_declination(answer):
        engagement.declined = True
        engagement.asked = True
        # Clear the stored names too, not just the returned pair: the caller
        # reads `engagement.get(sid).names`, so a name adopted earlier in the
        # session otherwise reached the quote after an explicit skip.
        engagement.client_name = None
        engagement.project_name = None
        return Resumption(
            request=request, names=(None, None), declined=True
        ), None
    elif loop is not None:
        confirmed = loop.process_answer(answer).slots
        engagement.client_name = confirmed.get("brief_client_name")
        engagement.project_name = confirmed.get("brief_project_name")

    if any(engagement.names):
        engagement.awaiting_confirmation = True
        engagement.pending_request = request
        return Resumption(
            request=request, names=engagement.names, declined=False
        ), format_confirmation(engagement.client_name, engagement.project_name)

    # Nothing usable came back. Treat it as a declination rather than re-asking.
    engagement.declined = True
    engagement.asked = True
    return Resumption(
        request=request, names=(None, None), declined=True
    ), None
