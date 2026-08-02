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
    return {
        "count": n,
        "latency_p50_ms": _pct(lat, 0.5),
        "latency_p95_ms": _pct(lat, 0.95),
        "total_tokens": sum(toks),
        "avg_tokens": (sum(toks) / len(toks)) if toks else None,
        "error_rate": (errors / n) if n else 0.0,
    }
