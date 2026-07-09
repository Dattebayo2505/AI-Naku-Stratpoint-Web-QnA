from __future__ import annotations

import logging
import re
import time

from stratpoint_rag._timing import log_total, timed
from stratpoint_rag.agent import tools as agent_tools
from stratpoint_rag.agent.agent import AgentResult, Link, run_agent
from stratpoint_rag.disambiguation.router import route
from stratpoint_rag.disambiguation.schemas import IntentCategory
from stratpoint_rag.guardrails.memory import ConversationMemory
from stratpoint_rag.guardrails.pipeline import GuardrailPipeline
from stratpoint_rag.guardrails.schemas import GuardrailConfig
from stratpoint_rag.rag.answer import answer_grounded, answer_grounded_stream

log = logging.getLogger(__name__)

_memories: dict[str, ConversationMemory] = {}

_RESOURCE_PATTERNS = re.compile(
    r"(pdf|whitepaper|white\s*paper|download|resource|document|report|brochure|"
    r"guide|ebook|e[- ]book|file|attachment|printable|readable)",
    re.IGNORECASE,
)

_HARMFUL_BLOCK = "I'm sorry, I can't help with that. Please ask me about Stratpoint's services or projects instead."

_INJECTION_BLOCK = "I can only answer questions about Stratpoint — their services, projects, technologies, and company information. Could you ask something related to Stratpoint?"

# After this many consecutive turns the bot can't answer (clarification asked
# or answer ungrounded), stop looping and hand off. Tracked on ConversationMemory
# because clarifications come from two paths — the router and the RAG LLM's own
# "not enough information" reply — so counting router messages alone misses half.
_MAX_CLARIFY_ROUNDS = 3

_ESCALATION_RESPONSE = (
    "It looks like I'm having trouble understanding what you need. I can help with "
    "Stratpoint's services, past projects, and technologies like OutSystems, Flutter, "
    "or cloud — or you can reach the team directly at https://stratpoint.com/contact-us/."
)


def _escalate_or_count(memory: ConversationMemory) -> bool:
    """Record a turn the bot couldn't answer. Returns True (and resets the
    streak) once the run of such turns reaches the cap — the caller then serves
    the hand-off instead of asking the user to clarify yet again."""
    if memory.clarify_streak >= _MAX_CLARIFY_ROUNDS:
        memory.clarify_streak = 0
        return True
    memory.clarify_streak += 1
    return False


def _escalation_for_answer(memory: ConversationMemory, is_grounded: bool | None) -> str | None:
    """Apply the clarify-streak rule to a completed answer turn. Returns the
    hand-off message if we've hit the cap, else None (keep the real answer).

    Only an explicit ``is_grounded is False`` (the LLM said "I don't have this")
    counts. ``None`` is ambiguous — a parse-fallback answer or a resource
    delivery that surfaced no grounded chunks may well have helped — so it
    leaves the streak untouched rather than pushing a helpful turn toward the
    hand-off."""
    if is_grounded is True:
        memory.clarify_streak = 0
    elif is_grounded is False and _escalate_or_count(memory):
        return _ESCALATION_RESPONSE
    return None


def _user_facing_block(reason: str) -> str:
    """Convert an internal guardrail reason into a conversational user-facing message."""
    if "NeMo" in reason:
        return reason
    lower = reason.lower()
    if any(kw in lower for kw in ("harmful", "attack", "malware", "hack", "exploit")):
        return _HARMFUL_BLOCK
    # Any remaining blocked category (e.g. 'system_prompt_request', which
    # contains none of the keywords above) gets the generic refusal — never
    # leak the raw internal reason string to the user.
    return _INJECTION_BLOCK


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
    """Run all available input guardrails (built-in keyword/PII → NeMo),
    returning (processed_input, block_reason).  block_reason is None if allowed.

    Built-in regex blocking and PII redaction run first — they catch obvious
    patterns in microseconds with zero API cost.  NeMo runs second as a more
    thorough LLM-powered fallback for nuanced cases the regex missed.
    The TopicFilter is skipped during the built-in pass because the
    disambiguation classifier handles relevance downstream.
    """
    text = message

    from stratpoint_rag.guardrails.input_guardrails import KeywordBlocker, PIIRedactor

    blocker = KeywordBlocker()
    br = blocker.check(text)
    if not br.passed:
        return text, br.message

    redactor = PIIRedactor()
    text, _ = redactor.redact(text)

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

    return text, None


def _run_output_guardrails(
    text: str,
    source_chunks: list,
    config: GuardrailConfig,
    use_nemo: bool,
) -> tuple[str, str | None]:
    """Run all available output guardrails (built-in → NeMo), returning
    (modified_text, block_reason).

    Built-in checks (AdviceBlocker, HallucinationChecker, OutputPIIChecker)
    run first — they use fast regex and embedding comparisons with zero
    extra API cost.  NeMo runs second as an LLM-powered policy gate for
    nuanced cases the built-in checks might miss.
    """

    builtin = GuardrailPipeline(config)
    text, results = builtin.run_output(text, source_chunks)
    for r in results:
        if r.action in ("block", "escalate"):
            log.warning("Built-in output rail blocked: %s", r.message)
            return text, r.message
        log.info("Built-in output rail passed: %s", r.message)

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
    stages: dict[str, float] = {}
    t_start = time.perf_counter()
    try:
        # ── Input guardrails (built-in keyword/PII → NeMo) ────────────────
        with timed("input_guardrails", stages):
            processed_input, block_reason = _run_input_guardrails(message, config, use_nemo)
        if block_reason:
            log.info("Input blocked: %s", block_reason)
            memory.add_turn("user", message)
            memory.add_turn("assistant", _user_facing_block(block_reason))
            return AgentResult(answer=_user_facing_block(block_reason), guardrail_reason=block_reason)

        # ── Disambiguation ────────────────────────────────────────────────
        with timed("disambiguation", stages):
            route_result = route(processed_input, session_memory=memory)

        if route_result.intent in (IntentCategory.HARMFUL, IntentCategory.OFF_TOPIC):
            reason = route_result.rejection_reason or "I can't process that request."
            memory.clarify_streak = 0  # definitive response, not persistent vagueness
            memory.add_turn("user", message)
            memory.add_turn("assistant", reason)
            return AgentResult(answer=reason, guardrail_reason=reason)

        if route_result.intent == IntentCategory.GREETING:
            reply = route_result.rejection_reason or ""
            memory.clarify_streak = 0
            memory.add_turn("user", message)
            memory.add_turn("assistant", reply)
            return AgentResult(answer=reply, guardrail_reason="Greeting detected")

        if route_result.clarification_question:
            # Persistent vagueness → hand off instead of asking the same thing again.
            if _escalate_or_count(memory):
                answer = _ESCALATION_RESPONSE
            else:
                answer = route_result.clarification_question
            memory.add_turn("user", message)
            memory.add_turn("assistant", answer)
            return AgentResult(
                answer=answer,
                guardrail_reason=f"Needed clarification: {route_result.intent.value}",
            )

        # ── Answer ────────────────────────────────────────────────────────
        source_chunks: list = []
        with timed("answer", stages):
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

        # ── Output guardrails (built-in → NeMo) ──────────────────────────
        with timed("output_guardrails", stages):
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

        # A grounded answer resets the streak; an explicit "not enough information"
        # (is_grounded False, incl. the RAG LLM's own reply) counts, and once
        # persistent is replaced by the hand-off — matching the clarification path.
        escalation = _escalation_for_answer(memory, result.is_grounded)
        if escalation:
            result.answer = escalation

        memory.add_turn("user", message)
        memory.add_turn("assistant", result.answer)

        return result
    finally:
        log_total(stages, (time.perf_counter() - t_start) * 1000)


# ── Streaming variant (SSE) ─────────────────────────────────────────────────

def _links_payload(links: list[Link]) -> list[dict]:
    return [{"title": l.title, "url": l.url} for l in links]


def stream_with_guardrails(
    message: str,
    history: list[dict] | None = None,
    *,
    agent=None,
    session_id: str | None = None,
    guardrail_config: GuardrailConfig | None = None,
    use_nemo: bool = True,
):
    """Event-generator twin of run_with_guardrails for SSE.

    Yields dict events:
      {"type": "status", "stage": "composing"|"searching"}   — progress hint
      {"type": "delta",  "text": "..."}                       — answer-text delta
      {"type": "done",   "answer","citations","resources",
                         "is_grounded","confidence","guardrail_reason"}

    IMPORTANT (safety): the streamed deltas are a PREVIEW of the raw LLM answer.
    Output guardrails run on the *complete* text afterward, and the terminal
    "done" event carries the authoritative, guardrail-safe answer. When a rail
    redacts/blocks, done.answer differs from the streamed preview and the client
    must replace what it showed. The direct RAG path token-streams; the ReAct
    (resource) path can't (multi-call) and is emitted as one delta before done.
    """
    memory = _get_memory(session_id)
    config = guardrail_config or GuardrailConfig()

    def _done(answer, *, citations=None, resources=None, is_grounded=None,
              confidence=None, guardrail_reason=None) -> dict:
        memory.add_turn("user", message)
        memory.add_turn("assistant", answer)
        return {
            "type": "done",
            "answer": answer,
            "citations": _links_payload(citations or []),
            "resources": _links_payload(resources or []),
            "is_grounded": is_grounded,
            "confidence": confidence,
            "guardrail_reason": guardrail_reason,
        }

    # ── Input guardrails ──────────────────────────────────────────────
    processed_input, block_reason = _run_input_guardrails(message, config, use_nemo)
    if block_reason:
        log.info("Input blocked (stream): %s", block_reason)
        yield _done(_user_facing_block(block_reason), guardrail_reason=block_reason)
        return

    # ── Disambiguation ────────────────────────────────────────────────
    route_result = route(processed_input, session_memory=memory)
    if route_result.intent in (IntentCategory.HARMFUL, IntentCategory.OFF_TOPIC):
        reason = route_result.rejection_reason or "I can't process that request."
        memory.clarify_streak = 0  # definitive response, not persistent vagueness
        yield _done(reason, guardrail_reason=reason)
        return
    if route_result.intent == IntentCategory.GREETING:
        memory.clarify_streak = 0
        yield _done(route_result.rejection_reason or "", guardrail_reason="Greeting detected")
        return
    if route_result.clarification_question:
        # Persistent vagueness → hand off instead of asking the same thing again
        # (parity with run_with_guardrails; the streaming UI is now the only path).
        answer = _ESCALATION_RESPONSE if _escalate_or_count(memory) else route_result.clarification_question
        yield _done(
            answer,
            guardrail_reason=f"Needed clarification: {route_result.intent.value}",
        )
        return

    # ── Answer ────────────────────────────────────────────────────────
    source_chunks: list = []
    citations: list[Link] = []
    resources: list[Link] = []
    is_grounded = None
    confidence = None

    if _wants_resource(message):
        # ReAct path — grounds inside tools, can't token-stream. Emit progress,
        # run it, then push the whole answer as one delta before the safe done.
        yield {"type": "status", "stage": "searching"}
        agent_tools.begin_capture()
        try:
            result = run_agent(message, history=history, agent=agent)
            source_chunks = agent_tools.captured_chunks()
            grounded_list = agent_tools.captured_grounded()
        finally:
            agent_tools.end_capture()
        answer_text = result.answer
        citations = result.citations
        resources = result.resources
        if grounded_list:
            g = min(grounded_list, key=lambda x: (x.confidence if x.confidence is not None else 1.0))
            is_grounded = g.is_grounded
            confidence = g.confidence
        if answer_text:
            yield {"type": "delta", "text": answer_text}
    else:
        yield {"type": "status", "stage": "composing"}
        query = message
        if route_result.slots and route_result.slots.get("topic") == "Contact / Location" and route_result.matched_keyword:
            from stratpoint_rag.rag.store import VectorStore
            store = VectorStore()
            got = store.col.get(where_document={"$contains": "office"}, include=["metadatas"])
            slugs = [m["slug"].replace("_", " ") for m in (got.get("metadatas") or [])]
            if slugs:
                query = f"{message} {' '.join(slugs[:10])}"
        gen = answer_grounded_stream(query, k=8)
        grounded = None
        try:
            while True:
                yield {"type": "delta", "text": next(gen)}
        except StopIteration as stop:
            answer_text, source_chunks, grounded = stop.value
        if grounded is not None:
            citations = [Link(title=c.title or "Stratpoint", url=c.url) for c in grounded.citations]
            is_grounded = grounded.is_grounded
            confidence = grounded.confidence

    # ── Output guardrails on the COMPLETE text (safety authority) ──────
    safe_text, out_block = _run_output_guardrails(answer_text, source_chunks, config, use_nemo)
    if out_block:
        log.warning("Output blocked (stream): %s", out_block)
        if config.mode == "fail_closed":
            yield _done(
                "I generated a response, but it failed safety checks. "
                "Please rephrase your question or contact our team for assistance.",
                guardrail_reason=out_block,
            )
            return
    elif safe_text != answer_text:
        answer_text = safe_text

    # Grounded answer resets the streak; a persistent "not enough information"
    # is replaced by the hand-off — parity with run_with_guardrails.
    escalation = _escalation_for_answer(memory, is_grounded)
    if escalation:
        answer_text = escalation

    yield _done(
        answer_text,
        citations=citations,
        resources=resources,
        is_grounded=is_grounded,
        confidence=confidence,
    )


def warmup(use_nemo: bool = True) -> None:
    """Pre-build the process-wide caches so the FIRST real request pays warm
    latency (~40s) instead of the ~137s cold path. Safe to call in a background
    thread at startup — each piece is the same lazy singleton the request path
    builds (embedder+Chroma, NeMo input/output rails incl. their expensive
    first-.check() init). Skips the answer LLM call (it has no cold component)."""
    import time as _time

    from stratpoint_rag._timing import note

    t0 = _time.perf_counter()
    try:
        from stratpoint_rag.rag.retrieve import retrieve
        retrieve("warmup", k=1)  # embedder + Chroma + HNSW graph
    except Exception as e:
        log.warning("warmup: retrieve failed: %s", e)
    cfg = GuardrailConfig()
    try:
        _run_input_guardrails("hello", cfg, use_nemo=use_nemo)
        _run_output_guardrails("Stratpoint builds software.", [], cfg, use_nemo=use_nemo)
    except Exception as e:
        log.warning("warmup: guardrail warm failed: %s", e)
    note(f"warmup complete in {(_time.perf_counter() - t0) * 1000:.0f} ms")
