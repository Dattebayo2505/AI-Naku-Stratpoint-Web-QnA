"""Token → USD estimate for the trace sink (Component #8, UoM discipline).

Rates are the published median for llama-3.1-8b-instruct across hosted
providers (source: artificialanalysis.ai/models/llama-3-1-instruct-8b, read
2026-08). They are a documented constant, not an invented one, so the "Unit of
Measurement" slide survives its first follow-up question. Override per-deploy
with LLMOPS_PRICE_PER_1K_PROMPT / LLMOPS_PRICE_PER_1K_COMPLETION.

Dependency-free, same as the rest of llmops.
"""

from __future__ import annotations

import os

DEFAULT_PRICE_PER_1K_PROMPT = 0.00008
DEFAULT_PRICE_PER_1K_COMPLETION = 0.00009

def _rate(env: str, default: float) -> float:
    val = os.getenv(env)
    try:
        return float(val) if val else default
    except ValueError:
        return default

def estimate_cost(prompt_tokens: int | None, completion_tokens: int | None) -> float | None:
    """USD for one request. None only when BOTH token counts are None — a known
    zero on one side still returns a number, so a completion-only call is not
    silently dropped from the cost column."""
    if prompt_tokens is None and completion_tokens is None:
        return None
    p = (prompt_tokens or 0) / 1000 * _rate("LLMOPS_PRICE_PER_1K_PROMPT", DEFAULT_PRICE_PER_1K_PROMPT)
    c = (completion_tokens or 0) / 1000 * _rate("LLMOPS_PRICE_PER_1K_COMPLETION", DEFAULT_PRICE_PER_1K_COMPLETION)
    return p + c
