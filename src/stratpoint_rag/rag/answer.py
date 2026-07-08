"""Integrated production prompt-engineered answer path (plan §7.9).

Calls the winning variant 'v4_combined_lowtemp' from the prompts package
and validates its structured JSON response against the Pydantic schema.
"""

from __future__ import annotations

import json
import logging
import httpx

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


def _strip_code_fences(s: str) -> str:
    """Strip a leading ```json / ``` fence and trailing ``` from a model reply.

    Without response_format=json_object (the reasoning-on path), NIM often wraps
    the JSON body in a markdown code fence, which breaks strict JSON parsing.
    Harmless no-op when no fence is present.
    """
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


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
    parse-failure fallback) and the model's native reasoning text (or None).

    When ``enable_reasoning`` is set, NIM's ``enable_thinking`` is requested. Note
    (live-confirmed): ``response_format=json_object`` SUPPRESSES reasoning, so in
    that mode we drop it and rely on the V4 prompt's JSON instruction; the parse
    fallback below covers any non-compliance.
    """
    key = config.nvidia_api_key()
    if not key:
        raise RuntimeError("NVIDIA_API_KEY is not set (see .envexample)")

    # 1. Retrieve the top-k relevant context chunks
    chunks = retrieve(query, k=k)

    # 2. Build the system and user prompts for the winning variant
    system_prompt, user_prompt = build_prompt(
        query, chunks, variant="v4_combined_lowtemp"
    )

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
    if enable_reasoning:
        body["chat_template_kwargs"] = {"enable_thinking": True}
    else:
        body["response_format"] = {"type": "json_object"}

    llm_timeout = config.llm_timeout()
    resp = httpx.post(
        f"{config.nvidia_base_url()}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json=body,
        timeout=llm_timeout,
    )
    resp.raise_for_status()
    message = resp.json()["choices"][0]["message"]
    raw_response = message["content"]
    # Defensive: live NIM returns "reasoning_content"; keep "reasoning" as a fallback.
    reasoning = message.get("reasoning_content") or message.get("reasoning")

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
