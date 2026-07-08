from __future__ import annotations

import logging
import re

from stratpoint_rag.guardrails.memory import ConversationMemory

from .clarification import ClarificationLoop
from .classifier import classify
from .schemas import IntentCategory, RouteResult
from .slots import extract_slots

log = logging.getLogger(__name__)

_GREETING_RESPONSE = "Hello! I'm the Stratpoint assistant. How can I help you with Stratpoint's services, projects, or technologies today?"

_OFF_TOPIC_RESPONSE = "I can only answer questions about Stratpoint — their services, projects, technologies, and company information. Could you ask something related to Stratpoint?"

_HARMFUL_RESPONSE = "I can't process that request. Please ask a question about Stratpoint's services or projects."

_DEFAULT_CLARIFY = "I'm not sure I understand. Could you tell me what you'd like to know about Stratpoint?"

_QUESTION_PATTERN = re.compile(r"^(what|how|why|when|where|which|who|does|do|is|are|can|could|would)", re.IGNORECASE)

# A substantive ask: a question, or an explicit request (esp. for a resource).
# When present, a missing hardcoded-topic slot must NOT force a clarification —
# the query is specific enough; let retrieval (RAG / find_resource) handle it.
_REQUEST_PATTERN = re.compile(
    r"\b(document|pdf|one[- ]?pager|whitepaper|white\s*paper|brochure|guide|ebook|"
    r"download|resource|report|send|share)\b",
    re.IGNORECASE,
)


def _is_specific_ask(user_input: str) -> bool:
    return (
        "?" in user_input
        or bool(_QUESTION_PATTERN.search(user_input))
        or bool(_REQUEST_PATTERN.search(user_input))
    )


def route(
    user_input: str,
    session_memory: ConversationMemory | None = None,
) -> RouteResult:
    if session_memory and not session_memory.is_empty:
        recent = session_memory.turns[-1] if session_memory.turns else None
        context = f"Previous exchange:\nUser: {recent.content if recent else ''}" if recent else None
    else:
        context = None

    intent_query = classify(user_input, conversation_context=context)

    if intent_query.confidence < 0.7 and intent_query.intent != IntentCategory.NEEDS_CLARIFICATION:
        is_question = "?" in user_input or bool(_QUESTION_PATTERN.search(user_input))
        if not is_question:
            intent_query.intent = IntentCategory.NEEDS_CLARIFICATION

    if intent_query.intent == IntentCategory.HARMFUL:
        return RouteResult(
            intent=IntentCategory.HARMFUL,
            confidence=intent_query.confidence,
            query=user_input,
            should_retrieve=False,
            rejection_reason=_HARMFUL_RESPONSE,
        )

    if intent_query.intent == IntentCategory.OFF_TOPIC:
        return RouteResult(
            intent=IntentCategory.OFF_TOPIC,
            confidence=intent_query.confidence,
            query=user_input,
            should_retrieve=False,
            rejection_reason=_OFF_TOPIC_RESPONSE,
        )

    if intent_query.intent == IntentCategory.GREETING:
        return RouteResult(
            intent=IntentCategory.GREETING,
            confidence=intent_query.confidence,
            query=user_input,
            should_retrieve=False,
            rejection_reason=_GREETING_RESPONSE,
        )

    if intent_query.intent == IntentCategory.NEEDS_CLARIFICATION:
        loop = ClarificationLoop(
            intent=IntentCategory.ASK_STRATPOINT,
            missing_slots=["topic"],
            max_turns=3,
        )
        question = loop.next_question()
        return RouteResult(
            intent=IntentCategory.NEEDS_CLARIFICATION,
            confidence=intent_query.confidence,
            query=user_input,
            should_retrieve=False,
            clarification_question=question or _DEFAULT_CLARIFY,
            clarification_session=loop.session,
        )

    slot_query = extract_slots(user_input, intent_query.intent)

    # Only clarify on a missing required slot when the input is genuinely vague.
    # A specific question or resource request must proceed to retrieval even if
    # its topic isn't in the hardcoded pattern list (regression: a QA-document
    # request was bounced to clarification because "quality assurance" had no
    # topic pattern). Truly vague input is already caught upstream as
    # NEEDS_CLARIFICATION before reaching here.
    if slot_query.missing_slots and not _is_specific_ask(user_input):
        loop = ClarificationLoop(
            intent=intent_query.intent,
            missing_slots=slot_query.missing_slots,
            max_turns=3,
        )
        loop.session.confirmed_slots.update(slot_query.slots)
        question = loop.next_question()
        return RouteResult(
            intent=intent_query.intent,
            confidence=intent_query.confidence,
            query=user_input,
            slots=slot_query.slots,
            should_retrieve=False,
            clarification_question=question,
            clarification_session=loop.session,
            matched_keyword=slot_query.matched_keyword,
        )

    return RouteResult(
        intent=intent_query.intent,
        confidence=intent_query.confidence,
        query=user_input,
        slots=slot_query.slots,
        should_retrieve=True,
        matched_keyword=slot_query.matched_keyword,
    )
