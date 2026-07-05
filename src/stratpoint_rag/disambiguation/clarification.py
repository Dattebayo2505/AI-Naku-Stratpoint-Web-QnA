from __future__ import annotations

import json
import logging

import httpx

from stratpoint_rag.rag import config

from .schemas import (
    ClarificationSession,
    ClarificationTurn,
    IntentCategory,
    SlotQuery,
)
from .slots import extract_slots

log = logging.getLogger(__name__)

CLARIFICATION_PROMPT = """You are a polite clarification assistant for a Stratpoint customer-support chatbot.

The user asked a question but some information is missing. Ask ONE natural, friendly question to get the missing information.

Intent: {intent}
Missing slots: {missing_slots}
Slot descriptions:
{slot_descriptions}

Previous clarification turns:
{history}

Ask exactly one question that feels natural in conversation. Do NOT list multiple questions — ask for only one missing slot at a time. Keep it brief and friendly.

Respond with a JSON object:
```json
{{
  "question": "Your single clarifying question here"
}}
```"""


class ClarificationLoop:
    def __init__(
        self,
        intent: IntentCategory,
        missing_slots: list[str],
        max_turns: int = 3,
        session: ClarificationSession | None = None,
    ):
        self.intent = intent
        self.missing_slots = missing_slots
        self.max_turns = max_turns

        if session:
            self.session = session
        else:
            self.session = ClarificationSession(
                intent=intent,
                max_turns=max_turns,
            )

    def next_question(self) -> str | None:
        if not self.missing_slots:
            return None

        if len(self.session.turns) >= self.max_turns:
            return None

        slot_name = self.missing_slots[0]
        slot_defs = _get_slot_descriptions(self.intent, self.missing_slots)

        history_text = "None so far."
        if self.session.turns:
            history_lines = [
                f"Assistant asked: {t.question}\nUser answered: {t.answer}"
                for t in self.session.turns
            ]
            history_text = "\n".join(history_lines)

        prompt = CLARIFICATION_PROMPT.format(
            intent=self.intent.value,
            missing_slots=", ".join(self.missing_slots),
            slot_descriptions=slot_defs,
            history=history_text,
        )

        try:
            key = config.nvidia_api_key()
            if not key:
                raise RuntimeError("NVIDIA_API_KEY is not set")

            resp = httpx.post(
                f"{config.nvidia_base_url()}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": config.llm_model(),
                    "messages": [
                        {"role": "system", "content": "You generate single clarifying questions. Respond with JSON only."},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 256,
                    "temperature": 0.3,
                    "response_format": {"type": "json_object"},
                    "stream": False,
                },
                timeout=30,
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"]
            data = json.loads(raw)
            return data.get("question")
        except Exception as e:
            log.warning("Failed to generate clarification question: %s", e)
            return self._fallback_question(slot_name)

    def _fallback_question(self, slot_name: str) -> str:
        fallbacks = {
            "topic": "What would you like to know about Stratpoint?",
            "project_name": "Do you have a specific Stratpoint project in mind?",
            "service_type": "What type of service are you interested in?",
        }
        return fallbacks.get(slot_name, f"Could you provide more information about {slot_name}?")

    def process_answer(self, answer: str) -> SlotQuery:
        if not self.missing_slots:
            return SlotQuery(
                intent=self.intent,
                slots=self.session.confirmed_slots,
                missing_slots=[],
            )

        current_slot = self.missing_slots[0]
        turn = ClarificationTurn(
            slot_name=current_slot,
            question=self.session.turns[-1].question if self.session.turns else "",
            answer=answer,
        )
        self.session.turns.append(turn)

        all_history = list(self.session.turns)
        slot_query = extract_slots(
            user_input=answer,
            intent=self.intent,
            history=all_history,
        )

        for name, value in slot_query.slots.items():
            if value is not None:
                self.session.confirmed_slots[name] = value

        self.missing_slots = [
            s for s in self.missing_slots
            if s not in self.session.confirmed_slots
        ]

        return SlotQuery(
            intent=self.intent,
            slots=self.session.confirmed_slots,
            missing_slots=self.missing_slots,
        )

    def is_complete(self) -> bool:
        if not self.missing_slots:
            return True
        if len(self.session.turns) >= self.max_turns:
            return True
        return False

    def to_dict(self) -> dict:
        return {
            "intent": self.intent.value,
            "missing_slots": list(self.missing_slots),
            "max_turns": self.max_turns,
            "session": self.session.model_dump(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> ClarificationLoop:
        session = ClarificationSession(**data["session"])
        return cls(
            intent=IntentCategory(data["intent"]),
            missing_slots=list(data["missing_slots"]),
            max_turns=data.get("max_turns", 3),
            session=session,
        )


def _get_slot_descriptions(intent: IntentCategory, slot_names: list[str]) -> str:
    from .slots import INTENT_SLOTS

    defs = INTENT_SLOTS.get(intent, [])
    lines = []
    for s in defs:
        if s.name in slot_names:
            req = "required" if s.required else "optional"
            lines.append(f"- {s.name} ({req}): {s.description}")
    return "\n".join(lines) if lines else "No slot descriptions available."
