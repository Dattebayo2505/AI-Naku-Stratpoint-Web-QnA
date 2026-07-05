from __future__ import annotations

import logging

from nemoguardrails.actions import action

from stratpoint_rag.guardrails.input_guardrails import PIIRedactor

log = logging.getLogger(__name__)

_pii_redactor = PIIRedactor()


@action(is_system_action=True)
async def check_pii_redaction(context: dict, text: str) -> str:
    redacted, matched = _pii_redactor.redact(text)
    if matched:
        log.info("PII redacted entities: %s", [r.entity_type for r in matched])
    return redacted


@action(is_system_action=True)
async def check_stratpoint_relevance(context: dict, text: str) -> bool:
    from stratpoint_rag.guardrails.input_guardrails import TopicFilter
    result = TopicFilter(use_llm_fallback=False).check(text)
    return result.passed


@action(is_system_action=True)
async def check_output_pii(context: dict, text: str) -> str:
    from stratpoint_rag.guardrails.output_guardrails import OutputPIIChecker

    source_chunks = context.get("source_chunks", [])
    result = OutputPIIChecker().check(text, source_chunks)
    if result.action == "redact":
        return result.modified_output or text
    return text


@action(is_system_action=True)
async def check_hallucination_custom(context: dict, text: str) -> bool:
    from stratpoint_rag.guardrails.output_guardrails import HallucinationChecker

    source_chunks = context.get("source_chunks", [])
    if not source_chunks:
        return True

    result = HallucinationChecker().check(text, source_chunks)
    return result.passed


@action(is_system_action=True)
async def check_advice_custom(context: dict, text: str) -> bool:
    from stratpoint_rag.guardrails.output_guardrails import AdviceBlocker
    from stratpoint_rag.rag.models import Chunk
    source_chunks = context.get("source_chunks", [])
    result = AdviceBlocker().check(text, source_chunks=source_chunks)
    return result.passed
