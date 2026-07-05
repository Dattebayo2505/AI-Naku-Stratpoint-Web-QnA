from __future__ import annotations

import logging

from .schemas import ClarificationSession, ClarificationTurn, IntentCategory, SlotQuery
from .slots import INTENT_SLOTS, extract_slots

log = logging.getLogger(__name__)

_FALLBACK_QUESTIONS = {
    "topic": "What would you like to know about Stratpoint?",
    "project_name": "Do you have a specific Stratpoint project in mind?",
    "service_type": "What type of service are you interested in?",
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

        if len(self.session.turns) == 0 and len(self.missing_slots) >= 2:
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
        slot_query = extract_slots(answer, self.intent, history=all_history)

        for name, value in slot_query.slots.items():
            if value is not None:
                self.session.confirmed_slots[name] = value

        self.missing_slots = [s for s in self.missing_slots if s not in self.session.confirmed_slots]

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
