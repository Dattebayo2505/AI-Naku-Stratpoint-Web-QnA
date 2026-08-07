from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel


class IntentCategory(str, Enum):
    ASK_STRATPOINT = "ask_stratpoint"
    # The visitor wants a scoped proposal for their OWN project — a quote, an
    # estimate, a PDF. Distinct from ASK_STRATPOINT ("tell me about Stratpoint")
    # because it is the one intent that needs a client/project name, and names
    # are the one thing neither docparse hop is allowed to supply.
    REQUEST_PROPOSAL = "request_proposal"
    GREETING = "greeting"
    OFF_TOPIC = "off_topic"
    NEEDS_CLARIFICATION = "needs_clarification"
    HARMFUL = "harmful"


class IntentQuery(BaseModel):
    intent: IntentCategory
    confidence: float
    reasoning: str
    sub_intent: str | None = None


class SlotDef(BaseModel):
    name: str
    description: str
    required: bool = True
    llm_hint: str | None = None


class SlotQuery(BaseModel):
    intent: IntentCategory
    slots: dict[str, Any] = {}
    missing_slots: list[str] = []
    matched_keyword: str | None = None


class ClarificationTurn(BaseModel):
    slot_name: str
    question: str
    answer: str


class ClarificationSession(BaseModel):
    turns: list[ClarificationTurn] = []
    max_turns: int = 3
    intent: IntentCategory | None = None
    confirmed_slots: dict[str, Any] = {}


class RouteResult(BaseModel):
    intent: IntentCategory
    confidence: float
    query: str
    slots: dict[str, Any] = {}
    should_retrieve: bool = True
    rejection_reason: str | None = None
    clarification_question: str | None = None
    clarification_session: ClarificationSession | None = None
    matched_keyword: str | None = None
