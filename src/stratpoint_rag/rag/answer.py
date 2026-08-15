"""Integrated production prompt-engineered answer path (plan §7.9).

Calls the winning variant 'v4_combined_lowtemp' from the prompts package
and validates its structured JSON response against the Pydantic schema.
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from stratpoint_rag import llmops
from stratpoint_rag.rag import config
from stratpoint_rag.rag.models import Chunk
from stratpoint_rag.rag.query_rewrite import contextualize_query
from stratpoint_rag.rag.retrieve import retrieve
from stratpoint_rag.prompts.builder import build_prompt
from stratpoint_rag.prompts.schema import Citation, GroundedAnswer

log = logging.getLogger(__name__)


# Chunks were halved (1600->800 chars) to stop single-fact dilution, so k is
# raised to keep the LLM's context budget ~constant (8*800 ~= the old 5*1600)
# and to give retrieval ranking margin for near-verbatim fact lookups.
_DEFAULT_K = 8


def _strip_code_fences(s: str | None) -> str:
    """Strip a leading ```json / ``` fence and trailing ``` from a model reply.

    Without response_format=json_object (the reasoning-on path), NIM often wraps
    the JSON body in a markdown code fence, which breaks strict JSON parsing.
    Harmless no-op when no fence is present.

    ``(s or "")`` matches the twin in ``docparse/extract.py``, which has always
    had the guard. A NIM reply can carry ``content: null`` — a filtered or
    empty completion — and a bare ``s.strip()`` raised AttributeError there.
    The caller's ``except`` swallowed it and returned ``None`` as the answer,
    which then failed ``AgentResult`` validation and surfaced as a 502.
    """
    s = (s or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


_REASONING_PREFIX = re.compile(r"^\s*Reasoning:\s*", re.IGNORECASE)


def _split_reasoning(raw: str) -> tuple[str, str | None]:
    """Split a 'Reasoning: ...' preamble off the front of a JSON reply.

    Returns (json_body, reasoning). If the preamble is absent — the model
    ignored the instruction — returns the input unchanged with None, so
    non-compliance degrades to 'no reasoning' rather than a parse failure.
    """
    s = (raw or "").strip()
    if not _REASONING_PREFIX.match(s):
        return raw, None
    brace = s.find("{")
    if brace == -1:
        return raw, None

    reasoning = _REASONING_PREFIX.sub("", s[:brace]).strip()
    # Without json_object mode the model often fences the body. Splitting at
    # '{' leaves the ```json OPENER on the tail of the reasoning text and the
    # closing fence on the tail of the body — neither is caught by
    # _strip_code_fences, which only handles a leading fence. Trim both.
    reasoning = re.sub(r"```(?:json)?\s*$", "", reasoning).strip()
    # Also trim any trailing "JSON Output:", "JSON:", or "Output:" label that
    # the model emitted right before the JSON object.
    reasoning = re.sub(r"(?i)\b(?:JSON\s+Output|JSON|Output)\s*:\s*$", "", reasoning).strip()
    body = s[brace:].rstrip()
    if body.endswith("```"):
        body = body[:-3].rstrip()
    return body, (reasoning or None)


def _extract_grounded_answer(raw_response: str | None) -> GroundedAnswer | None:
    """Extract and validate a GroundedAnswer from a raw model response.

    Handles:
    - Direct JSON string matching GroundedAnswer
    - Markdown fenced JSON (```json ... ```)
    - Multiple JSON blocks / schema definitions echoed before the instance JSON
    - Preamble/postamble text surrounding the JSON
    """
    if not raw_response:
        return None

    cleaned = _strip_code_fences(raw_response)

    # 1. Direct validation attempt
    try:
        return GroundedAnswer.model_validate_json(cleaned)
    except Exception:
        pass

    # 2. Extract from markdown code fences if present
    fence_pattern = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
    for match in fence_pattern.finditer(raw_response):
        block = match.group(1).strip()
        try:
            return GroundedAnswer.model_validate_json(block)
        except Exception:
            try:
                data = json.loads(block)
                if isinstance(data, dict):
                    return GroundedAnswer.model_validate(data)
            except Exception:
                pass

    # 3. Scan for top-level JSON objects using JSONDecoder (handles echoed schema + instance JSON)
    decoder = json.JSONDecoder()
    candidates: list[GroundedAnswer] = []
    idx = 0
    while idx < len(cleaned):
        brace_pos = cleaned.find("{", idx)
        if brace_pos == -1:
            break
        try:
            obj, end_pos = decoder.raw_decode(cleaned, idx=brace_pos)
            idx = max(end_pos, brace_pos + 1)
            if isinstance(obj, dict):
                try:
                    candidates.append(GroundedAnswer.model_validate(obj))
                except Exception:
                    pass
        except Exception:
            idx = brace_pos + 1

    if candidates:
        return candidates[-1]

    return None


def _dedupe_citations(citations: list[Citation]) -> list[Citation]:
    """Deduplicate citations by normalized URL (or title if URL is empty), preserving order.

    Normalizes URLs by trimming whitespace and trailing slashes. If multiple citations
    share the same normalized URL, only the first occurrence is kept.
    """
    seen: set[str] = set()
    deduped: list[Citation] = []
    for c in citations:
        url = (c.url or "").strip().rstrip("/")
        if url:
            key = f"url:{url}"
        else:
            title = (c.title or "").strip()
            key = f"title:{title}" if title else f"obj:{id(c)}"
        if key not in seen:
            seen.add(key)
            deduped.append(c)
    return deduped


def answer(
    query: str, k: int = _DEFAULT_K, history: list[dict] | list | None = None
) -> tuple[str, list[Chunk]]:
    """Backward-compatible 2-tuple seam (used by agent tools).

    Delegates to answer_grounded and drops the parsed GroundedAnswer + reasoning.
    """
    text, chunks, _, _ = answer_grounded(query, k, history=history)
    return text, chunks


def answer_grounded(
    query: str,
    k: int = _DEFAULT_K,
    enable_reasoning: bool = False,
    history: list[dict] | list | None = None,
) -> tuple[str, list[Chunk], GroundedAnswer | None, str | None]:
    """Like answer(), but also returns the parsed GroundedAnswer (or None on
    parse-failure fallback) and the model's reasoning text (or None).

    When ``enable_reasoning`` is set, the reasoning prompt variant is used and
    ``response_format`` is dropped (json_object mode forbids the prose
    preamble). The model's ``Reasoning:`` line is split off and returned as the
    4th element; the remainder is parsed as the GroundedAnswer JSON.
    """
    key = config.nvidia_api_key()
    if not key:
        raise RuntimeError("NVIDIA_API_KEY is not set (see .envexample)")

    # 1. Retrieve the top-k relevant context chunks (contextualized if follow-up)
    retrieval_query = contextualize_query(query, history=history)
    chunks = retrieve(retrieval_query, k=k)

    # 2. Build the system and user prompts. Reasoning is prompted, not native:
    #    NIM's endpoint for meta/llama-3.1-8b-instruct does not support
    #    enable_thinking, so the reasoning variant asks for a 'Reasoning:'
    #    preamble ahead of the JSON and we split it off below.
    variant = "v4_combined_reasoning" if enable_reasoning else "v4_combined_lowtemp"
    system_prompt, user_prompt = build_prompt(query, chunks, variant=variant)

    # 3. Call the NVIDIA NIM endpoint with conversation history if present
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    if history:
        for h in history:
            role = getattr(h, "role", None) or (h.get("role") if isinstance(h, dict) else None)
            content = getattr(h, "content", None) or (h.get("content") if isinstance(h, dict) else None)
            if role and content:
                messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_prompt})

    body = {
        "model": config.llm_model(),
        "messages": messages,
        "max_tokens": 4096,
        "temperature": 0.1,
        "top_p": 0.95,
        "stream": False,
    }
    # json_object mode forbids the prose preamble, so it is only used when
    # reasoning is off — where it keeps its hard JSON guarantee.
    if not enable_reasoning:
        body["response_format"] = {"type": "json_object"}

    llm_timeout = config.llm_timeout()
    resp = httpx.post(
        f"{config.nvidia_base_url()}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json=body,
        timeout=llm_timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    llmops.add_usage(data.get("usage"))  # per-request token accumulator
    message = data["choices"][0]["message"]
    # `or ""` for the same reason as the guard in _strip_code_fences: the key is
    # present but null on a filtered or empty completion, and every downstream
    # consumer here is typed as str.
    raw_response = message.get("content") or ""

    reasoning = None
    if enable_reasoning:
        raw_response, reasoning = _split_reasoning(raw_response)

    # 4. Parse and validate the response (tolerate markdown-fenced JSON, schema
    #    echoes, or multiple JSON blocks on the reasoning-on path where
    #    json_object mode is disabled).
    parsed = _extract_grounded_answer(raw_response)
    if parsed is None:
        log.warning("JSON parsing failed, falling back to raw response: %s", raw_response)
        return raw_response, chunks, None, reasoning

    # 5. Format answer and citations
    text = parsed.answer

    if parsed.citations:
        parsed.citations = _dedupe_citations(parsed.citations)
        citations_list = []
        for c in parsed.citations:
            title = c.title if c.title else "Stratpoint"
            citations_list.append(f"- {title} ({c.url})")
        citations_str = "\n\nSources used:\n" + "\n".join(citations_list)
        return f"{text}{citations_str}", chunks, parsed, reasoning

    return text, chunks, parsed, reasoning
