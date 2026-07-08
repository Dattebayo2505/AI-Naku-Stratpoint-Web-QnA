from __future__ import annotations

import logging
import re

from stratpoint_rag.agent import tools as agent_tools
from stratpoint_rag.agent.agent import AgentResult, Link, run_agent
from stratpoint_rag.disambiguation.router import route
from stratpoint_rag.disambiguation.schemas import IntentCategory
from stratpoint_rag.guardrails.memory import ConversationMemory
from stratpoint_rag.guardrails.pipeline import GuardrailPipeline
from stratpoint_rag.guardrails.schemas import GuardrailConfig
from stratpoint_rag.rag.answer import answer_grounded

log = logging.getLogger(__name__)

_memories: dict[str, ConversationMemory] = {}

_RESOURCE_PATTERNS = re.compile(
    r"(pdf|whitepaper|white\s*paper|download|resource|document|report|brochure|"
    r"guide|ebook|e[- ]book|file|attachment|printable|readable)",
    re.IGNORECASE,
)

_HARMFUL_BLOCK = "I'm sorry, I can't help with that. Please ask me about Stratpoint's services or projects instead."

_INJECTION_BLOCK = "I can only answer questions about Stratpoint — their services, projects, technologies, and company information. Could you ask something related to Stratpoint?"


def _user_facing_block(reason: str) -> str:
    """Convert an internal guardrail reason into a conversational user-facing message."""
    if "NeMo" in reason:
        return reason
    lower = reason.lower()
    if any(kw in lower for kw in ("harmful", "attack", "malware", "hack", "exploit")):
        return _HARMFUL_BLOCK
    if any(kw in lower for kw in ("injection", "jailbreak", "bypass", "leak", "override", "info")):
        return _INJECTION_BLOCK
    return reason


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


def _run_input_guardrails(
    message: str,
    config: GuardrailConfig,
    use_nemo: bool,
) -> tuple[str, str | None]:
    """Run all available input guardrails (NeMo → keyword/PII supplement),
    returning (processed_input, block_reason).  block_reason is None if allowed.

    The supplementary pass only does regex blocking and PII redaction — the
    TopicFilter (which can call the LLM) is skipped because the disambiguation
    classifier handles relevance downstream.
    """
    text = message

    if use_nemo:
        try:
            from stratpoint_rag.guardrails.nemo_guardrails import NeMoGuardrailPipeline
            nemo = NeMoGuardrailPipeline(config)
            text, results = nemo.run_input(text)
            for r in results:
                if r.action == "block":
                    return text, r.message
        except ImportError:
            pass

    from stratpoint_rag.guardrails.input_guardrails import KeywordBlocker, PIIRedactor

    blocker = KeywordBlocker()
    br = blocker.check(text)
    if not br.passed:
        return text, br.message

    redactor = PIIRedactor()
    text, _ = redactor.redact(text)

    return text, None


def _run_output_guardrails(
    text: str,
    source_chunks: list,
    config: GuardrailConfig,
    use_nemo: bool,
) -> tuple[str, str | None]:
    """Run all available output guardrails (NeMo → built-in), returning
    (modified_text, block_reason)."""

    if use_nemo:
        try:
            from stratpoint_rag.guardrails.nemo_guardrails import NeMoGuardrailPipeline
            nemo = NeMoGuardrailPipeline(config)
            text, results = nemo.run_output(text, source_chunks)
            for r in results:
                if r.action in ("block", "escalate"):
                    log.warning("NeMo output rail blocked: %s", r.message)
                    return text, r.message
                log.info("NeMo output rail passed: %s", r.message)
        except ImportError:
            log.info("NeMo not available; skipping NeMo output rails")

    builtin = GuardrailPipeline(config)
    text, results = builtin.run_output(text, source_chunks)
    for r in results:
        if r.action in ("block", "escalate"):
            log.warning("Built-in output rail blocked: %s", r.message)
            return text, r.message
        log.info("Built-in output rail passed: %s", r.message)

    return text, None


def run_with_guardrails(
    message: str,
    history: list[dict] | None = None,
    *,
    agent=None,
    session_id: str | None = None,
    guardrail_config: GuardrailConfig | None = None,
    use_nemo: bool = True,
    enable_reasoning: bool = False,
) -> AgentResult:
    memory = _get_memory(session_id)
    config = guardrail_config or GuardrailConfig()

    # ── Input guardrails (NeMo → built-in) ────────────────────────────
    processed_input, block_reason = _run_input_guardrails(message, config, use_nemo)
    if block_reason:
        log.info("Input blocked: %s", block_reason)
        memory.add_turn("user", message)
        memory.add_turn("assistant", _user_facing_block(block_reason))
        return AgentResult(answer=_user_facing_block(block_reason), guardrail_reason=block_reason)

    # ── Disambiguation ────────────────────────────────────────────────
    route_result = route(processed_input, session_memory=memory)

    if route_result.intent in (IntentCategory.HARMFUL, IntentCategory.OFF_TOPIC):
        reason = route_result.rejection_reason or "I can't process that request."
        memory.add_turn("user", message)
        memory.add_turn("assistant", reason)
        return AgentResult(answer=reason, guardrail_reason=reason)

    if route_result.intent == IntentCategory.GREETING:
        reply = route_result.rejection_reason or ""
        memory.add_turn("user", message)
        memory.add_turn("assistant", reply)
        return AgentResult(answer=reply, guardrail_reason="Greeting detected")

    if route_result.clarification_question:
        memory.add_turn("user", message)
        memory.add_turn("assistant", route_result.clarification_question)
        return AgentResult(
            answer=route_result.clarification_question,
            guardrail_reason=f"Needed clarification: {route_result.intent.value}",
        )

    # ── Answer ────────────────────────────────────────────────────────
    source_chunks: list = []
    if _wants_resource(message):
        # The ReAct agent grounds inside its tools; capture the chunks it
        # retrieves so the output guardrails can actually verify the answer
        # (otherwise the hallucination check sees no source and blocks it).
        agent_tools.begin_capture()
        try:
            result = run_agent(
                message, history=history, agent=agent, enable_reasoning=enable_reasoning
            )
            source_chunks = agent_tools.captured_chunks()
            grounded_list = agent_tools.captured_grounded()
        finally:
            agent_tools.end_capture()
        if grounded_list:
            # Conservative: report the least-confident grounded result across
            # any search_stratpoint calls this turn.
            g = min(
                grounded_list,
                key=lambda x: (x.confidence if x.confidence is not None else 1.0),
            )
            result.is_grounded = g.is_grounded
            result.confidence = g.confidence
    else:
        query = message
        if route_result.slots and route_result.slots.get("topic") == "Contact / Location" and route_result.matched_keyword:
            from stratpoint_rag.rag.store import VectorStore
            store = VectorStore()
            got = store.col.get(where_document={"$contains": "office"}, include=["metadatas"])
            slugs = [m["slug"].replace("_", " ") for m in (got.get("metadatas") or [])]
            if slugs:
                query = f"{message} {' '.join(slugs[:10])}"
        raw, source_chunks, grounded, reasoning = answer_grounded(query, k=8, enable_reasoning=enable_reasoning)
        result = AgentResult(answer=raw)
        result.reasoning = reasoning
        if grounded is not None:
            result.citations = [
                Link(title=c.title or "Stratpoint", url=c.url)
                for c in grounded.citations
            ]
            result.is_grounded = grounded.is_grounded
            result.confidence = grounded.confidence

    # ── Output guardrails (NeMo → built-in) ──────────────────────────
    safe_text, out_block = _run_output_guardrails(result.answer, source_chunks, config, use_nemo)
    if out_block:
        log.warning("Output blocked: %s", out_block)
        if config.mode == "fail_closed":
            result.answer = (
                "I generated a response, but it failed safety checks. "
                "Please rephrase your question or contact our team for assistance."
            )
            result.guardrail_reason = out_block
            return result
    elif safe_text != result.answer:
        result.answer = safe_text

    memory.add_turn("user", message)
    memory.add_turn("assistant", result.answer)

    return result
