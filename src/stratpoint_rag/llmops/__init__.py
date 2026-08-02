"""LLMOps monitoring (Component #8) — traces, latency, token usage, errors.

Dependency-free by design: callers pass primitives, this package just persists
and aggregates. See docs/plan-llmops.md.
"""

from __future__ import annotations

from datetime import datetime, timezone

from stratpoint_rag.llmops.metrics import aggregate
from stratpoint_rag.llmops.sink import append, enabled, read_records
from stratpoint_rag.llmops.usage import add_usage, pop_usage, reset_usage


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def record(
    path: str,
    latency_ms: float,
    *,
    error: str | None = None,
    session_id: str | None = None,
    model: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    tool_calls: list[str] | None = None,
    is_grounded: bool | None = None,
    confidence: float | None = None,
) -> None:
    """Persist one request's telemetry. Query text is deliberately omitted (PII)."""
    append(
        {
            "ts": _now(),
            "session_id": session_id,
            "path": path,
            "model": model,
            "latency_ms": round(latency_ms),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "tool_calls": tool_calls or [],
            "error": error,
            "is_grounded": is_grounded,
            "confidence": confidence,
        }
    )


__all__ = [
    "record",
    "append",
    "read_records",
    "aggregate",
    "enabled",
    "add_usage",
    "pop_usage",
    "reset_usage",
]
