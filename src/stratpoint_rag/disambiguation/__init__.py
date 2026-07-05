"""Disambiguation: detect ambiguous user input, classify intent, extract
slots, and run a multi-turn clarification loop before committing to retrieval.
"""

from __future__ import annotations

from .classifier import classify
from .clarification import ClarificationLoop
from .router import route
from .schemas import (
    ClarificationSession,
    ClarificationTurn,
    IntentCategory,
    IntentQuery,
    RouteResult,
    SlotDef,
    SlotQuery,
)
from .slots import INTENT_SLOTS, extract_slots

__all__ = [
    "classify",
    "ClarificationLoop",
    "route",
    "ClarificationSession",
    "ClarificationTurn",
    "IntentCategory",
    "IntentQuery",
    "RouteResult",
    "SlotDef",
    "SlotQuery",
    "INTENT_SLOTS",
    "extract_slots",
]
