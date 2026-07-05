from __future__ import annotations

import logging
import re

from stratpoint_rag.agent.agent import AgentResult, run_agent
from stratpoint_rag.disambiguation.router import route
from stratpoint_rag.disambiguation.schemas import IntentCategory
from stratpoint_rag.guardrails.memory import ConversationMemory
from stratpoint_rag.guardrails.pipeline import GuardrailPipeline
from stratpoint_rag.guardrails.schemas import GuardrailConfig
from stratpoint_rag.rag.answer import answer as rag_answer

log = logging.getLogger(__name__)

_memories: dict[str, ConversationMemory] = {}

_RESOURCE_PATTERNS = re.compile(
    r"(pdf|whitepaper|white\s*paper|download|resource|document|report|brochure|"
    r"guide|ebook|e[- ]book|file|attachment|printable|readable)",
    re.IGNORECASE,
)


def _wants_resource(message: str) -> bool:
    return bool(_RESOURCE_PATTERNS.search(message))


def _get_memory(session_id: str | None = None) -> ConversationMemory:
    sid = session_id or "default"
    if sid not in _memories:
        _memories[sid] = ConversationMemory(session_id=sid)
    return _memories[sid]


def clear_memory(session_id: str | None = None) -> None:
    sid = session_id or "default"
    _memories.pop(sid, None)


def run_with_guardrails(
    message: str,
    history: list[dict] | None = None,
    *,
    agent=None,
    session_id: str | None = None,
    guardrail_config: GuardrailConfig | None = None,
    use_nemo: bool = True,
) -> AgentResult:
    memory = _get_memory(session_id)

    if use_nemo:
        try:
            from stratpoint_rag.guardrails.nemo_guardrails import NeMoGuardrailPipeline
            guardrails = NeMoGuardrailPipeline(guardrail_config or GuardrailConfig())
        except ImportError:
            log.warning("NeMo not available, falling back to built-in guardrails")
            guardrails = GuardrailPipeline(guardrail_config or GuardrailConfig())
    else:
        guardrails = GuardrailPipeline(guardrail_config or GuardrailConfig())

    processed_input, input_results = guardrails.run_input(message)

    for r in input_results:
        if r.action == "block":
            log.info("Input blocked: %s", r.message)
            memory.add_turn("user", message)
            memory.add_turn("assistant", r.message)
            return AgentResult(answer=r.message)

    route_result = route(processed_input, session_memory=memory)

    if route_result.intent in (IntentCategory.HARMFUL, IntentCategory.OFF_TOPIC):
        memory.add_turn("user", message)
        memory.add_turn("assistant", route_result.rejection_reason or "")
        return AgentResult(answer=route_result.rejection_reason or "")

    if route_result.intent == IntentCategory.GREETING:
        memory.add_turn("user", message)
        memory.add_turn("assistant", route_result.rejection_reason or "")
        return AgentResult(answer=route_result.rejection_reason or "")

    if route_result.clarification_question:
        memory.add_turn("user", message)
        memory.add_turn("assistant", route_result.clarification_question)
        return AgentResult(answer=route_result.clarification_question)

    source_chunks: list = []
    if _wants_resource(message):
        result = run_agent(message, history=history, agent=agent)
    else:
        raw, source_chunks = rag_answer(message)
        result = AgentResult(answer=raw)

    final_output, output_results = guardrails.run_output(result.answer, source_chunks)

    for r in output_results:
        if r.action in ("block", "escalate"):
            log.warning("Output blocked: %s", r.message)
            if guardrail_config and guardrail_config.mode == "fail_closed":
                result.answer = (
                    "I generated a response, but it failed safety checks. "
                    "Please rephrase your question or contact our team for assistance."
                )
                return result

    if final_output != result.answer:
        result.answer = final_output

    memory.add_turn("user", message)
    memory.add_turn("assistant", result.answer)

    return result
