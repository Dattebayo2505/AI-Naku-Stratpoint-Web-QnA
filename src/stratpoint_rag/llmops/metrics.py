"""Aggregate metrics over trace records (see docs/plan-llmops.md).

The three headline numbers the rubric wants (§6.1): latency p50/p95, token
usage, error rate. Per-request drill-down (tool_calls, grounding) stays on the
raw records. Pure stdlib — no numpy.
"""

from __future__ import annotations

from math import ceil, floor


def _pct(sorted_vals: list[float], p: float) -> float | None:
    """Linear-interpolated percentile (p in [0,1]) over a sorted list."""
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p
    f, c = floor(k), ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


def aggregate(records: list[dict]) -> dict:
    n = len(records)
    lat = sorted(r["latency_ms"] for r in records if r.get("latency_ms") is not None)
    toks = [r["total_tokens"] for r in records if r.get("total_tokens") is not None]
    errors = sum(1 for r in records if r.get("error"))
    grounded = [r["is_grounded"] for r in records if r.get("is_grounded") is not None]
    confs = [r["confidence"] for r in records if r.get("confidence") is not None]
    # Counts ANY non-empty guardrail_reason, not only safety blocks:
    # agent/guardrail_agent.py also sets it for ordinary routing — greeting
    # detection, "asked how to name the proposal", clarification prompts.
    # The key/computation stays as-is because other code and tests depend on
    # it; the UI relabels it "Guardrail/routing intercepts" instead of
    # renaming this field.
    fired = sum(1 for r in records if r.get("guardrail_reason"))
    costs = [r["cost_usd"] for r in records if r.get("cost_usd") is not None]
    return {
        "count": n,
        "latency_p50_ms": _pct(lat, 0.5),
        "latency_p95_ms": _pct(lat, 0.95),
        "total_tokens": sum(toks),
        "avg_tokens": (sum(toks) / len(toks)) if toks else None,
        "error_rate": (errors / n) if n else 0.0,
        "grounded_rate": (sum(1 for g in grounded if g) / len(grounded)) if grounded else None,
        "mean_confidence": (sum(confs) / len(confs)) if confs else None,
        "guardrail_fire_rate": (fired / n) if n else 0.0,
        "total_cost_usd": sum(costs),
    }
