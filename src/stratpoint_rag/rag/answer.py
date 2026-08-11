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
from stratpoint_rag.rag.retrieve import retrieve
from stratpoint_rag.prompts.builder import build_prompt
from stratpoint_rag.prompts.schema import GroundedAnswer

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
    body = s[brace:].rstrip()
    if body.endswith("```"):
        body = body[:-3].rstrip()
    return body, (reasoning or None)


def answer(query: str, k: int = _DEFAULT_K) -> tuple[str, list[Chunk]]:
    """Backward-compatible 2-tuple seam (used by agent tools).

    Delegates to answer_grounded and drops the parsed GroundedAnswer + reasoning.
    """
    text, chunks, _, _ = answer_grounded(query, k)
    return text, chunks


def answer_grounded(
    query: str, k: int = _DEFAULT_K, enable_reasoning: bool = False
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

    # 1. Retrieve the top-k relevant context chunks
    chunks = retrieve(query, k=k)

    # 2. Build the system and user prompts. Reasoning is prompted, not native:
    #    NIM's endpoint for meta/llama-3.1-8b-instruct does not support
    #    enable_thinking, so the reasoning variant asks for a 'Reasoning:'
    #    preamble ahead of the JSON and we split it off below.
    variant = "v4_combined_reasoning" if enable_reasoning else "v4_combined_lowtemp"
    system_prompt, user_prompt = build_prompt(query, chunks, variant=variant)

    # 3. Call the NVIDIA NIM endpoint
    body = {
        "model": config.llm_model(),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
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

    # 4. Parse and validate the response (tolerate markdown-fenced JSON, which
    #    appears on the reasoning-on path where json_object mode is disabled).
    try:
        parsed = GroundedAnswer.model_validate_json(_strip_code_fences(raw_response))
    except Exception as e:
        log.warning("JSON parsing failed, falling back to raw response: %s", e)
        return raw_response, chunks, None, reasoning

    # 5. Format answer and citations
    text = parsed.answer

    if parsed.citations:
        citations_list = []
        for c in parsed.citations:
            title = c.title if c.title else "Stratpoint"
            citations_list.append(f"- {title} ({c.url})")
        citations_str = "\n\nSources used:\n" + "\n".join(citations_list)
        return f"{text}{citations_str}", chunks, parsed, reasoning

    return text, chunks, parsed, reasoning
