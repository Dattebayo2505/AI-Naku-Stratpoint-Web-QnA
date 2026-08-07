from __future__ import annotations

import logging

from .schemas import ClarificationSession, ClarificationTurn, IntentCategory, SlotQuery
from .slots import INTENT_SLOTS, extract_slots

log = logging.getLogger(__name__)

_FALLBACK_QUESTIONS = {
    "topic": "What would you like to know about Stratpoint?",
    "project_name": "Do you have a specific Stratpoint project in mind?",
    "service_type": "What type of service are you interested in?",
    # Both offer declining in the question itself. The proposal can be produced
    # without either name, so an answer of "leave them blank" has to read as a
    # normal choice rather than as a refusal to cooperate.
    "brief_client_name": (
        "Before I put this together — is there a client or company name you'd "
        "like on the proposal? You can also say 'skip' to leave it blank."
    ),
    "brief_project_name": (
        "And a project name for the proposal? 'Skip' is fine if you'd rather "
        "leave it blank."
    ),
}

_MULTI_TURN_GREETING = (
    "I'd be happy to help you with information about Stratpoint! "
    "What would you like to know about their services, projects, or technologies?"
)

_HIGHER_LEVEL = (
    "I'm here to answer questions about Stratpoint — their software development services, "
    "technology expertise (like OutSystems, Flutter, or cloud), and past projects. "
    "What would you like to explore?"
)


class ClarificationLoop:
    def __init__(
        self,
        intent: IntentCategory,
        missing_slots: list[str],
        max_turns: int = 3,
        session: ClarificationSession | None = None,
    ):
        self.intent = intent
        self.missing_slots = list(missing_slots)
        self.max_turns = max_turns
        self.session = session or ClarificationSession(intent=intent, max_turns=max_turns)

    def next_question(self) -> str | None:
        if not self.missing_slots:
            return None

        if len(self.session.turns) >= self.max_turns:
            return None

        slot_name = self.missing_slots[0]

        # The "what would you like to explore?" opener is an ASK_STRATPOINT
        # message. Serving it for a proposal's naming question would read as the
        # bot losing the thread of a conversation it started.
        if (
            self.intent == IntentCategory.ASK_STRATPOINT
            and len(self.session.turns) == 0
            and len(self.missing_slots) >= 2
        ):
            return _HIGHER_LEVEL

        return _FALLBACK_QUESTIONS.get(slot_name)

    def process_answer(self, answer: str) -> SlotQuery:
        if not self.missing_slots:
            return SlotQuery(intent=self.intent, slots=self.session.confirmed_slots, missing_slots=[])

        current_slot = self.missing_slots[0]

        turn = ClarificationTurn(
            slot_name=current_slot,
            question=self.session.turns[-1].question if self.session.turns else "",
            answer=answer,
        )
        self.session.turns.append(turn)

        all_history = list(self.session.turns)
        # target_slot tells the extractor which slot an unlabelled answer
        # belongs to — the visitor typing a bare name at a question about the
        # client name means the client name.
        slot_query = extract_slots(
            answer, self.intent, history=all_history, target_slot=current_slot
        )

        for name, value in slot_query.slots.items():
            if value is not None:
                self.session.confirmed_slots[name] = value

        self.missing_slots = [s for s in self.missing_slots if s not in self.session.confirmed_slots]

        # An optional slot that has been asked is finished, whatever came back.
        # Without this a declination leaves the slot "missing" and the loop asks
        # again — which is precisely the coercion required=False exists to avoid.
        if self.intent == IntentCategory.REQUEST_PROPOSAL:
            self.missing_slots = [s for s in self.missing_slots if s != current_slot]

        return SlotQuery(
            intent=self.intent,
            slots=self.session.confirmed_slots,
            missing_slots=self.missing_slots,
        )

    def is_complete(self) -> bool:
        return not self.missing_slots or len(self.session.turns) >= self.max_turns

    def to_dict(self) -> dict:
        return {
            "intent": self.intent.value,
            "missing_slots": list(self.missing_slots),
            "max_turns": self.max_turns,
            "session": self.session.model_dump(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> ClarificationLoop:
        return cls(
            intent=IntentCategory(data["intent"]),
            missing_slots=list(data["missing_slots"]),
            max_turns=data.get("max_turns", 3),
            session=ClarificationSession(**data["session"]),
        )
