"""Per-request token accumulator (thread-local).

The agent path calls NIM several times per turn (the ReAct loop) and nests a
RAG call inside the search_stratpoint tool, so token usage can't be read from a
single response. Instead every LLM call site calls add_usage(); the request
boundary (the API handler) resets before the turn and pops the total after.

One request runs synchronously on one worker thread, so the thread-local scopes
to exactly that request — including LLM calls nested inside agent tools.
"""

from __future__ import annotations

import threading

_tls = threading.local()
_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")


def reset_usage() -> None:
    _tls.acc = {k: 0 for k in _KEYS}
    _tls.seen = False


def add_usage(usage: dict | None) -> None:
    """Add one NIM `usage` block to this thread's running total (no-op if None)."""
    if not usage:
        return
    acc = getattr(_tls, "acc", None)
    if acc is None:
        reset_usage()
        acc = _tls.acc
    _tls.seen = True
    for k in _KEYS:
        acc[k] += usage.get(k, 0) or 0


def pop_usage() -> dict | None:
    """Return (and clear) the running total. None if no usage was seen."""
    acc = getattr(_tls, "acc", None)
    seen = getattr(_tls, "seen", False)
    _tls.acc = None
    _tls.seen = False
    return acc if seen else None
