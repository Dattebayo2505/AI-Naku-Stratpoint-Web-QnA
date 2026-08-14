"""LLMOps store + metrics + usage-accumulator self-checks.

The store is MLflow (sqlite backend); no network, no LLM. The round-trip tests
are the load-bearing ones: MLflow params are *strings*, so anything that is not
JSON-encoded on the way in comes back as text — and `metrics.aggregate` counts
`is_grounded` for truthiness, where the string "False" is True. That bug would
report a 100% grounded rate forever, silently.
"""

from __future__ import annotations

import threading

import pytest

from stratpoint_rag import llmops
from stratpoint_rag.llmops import metrics, sink, usage


def test_record_roundtrip():
    llmops.record("/chat", 1234.7, model="m", total_tokens=100, is_grounded=True)
    recs = llmops.read_records()
    assert len(recs) == 1
    assert recs[0]["path"] == "/chat"
    assert recs[0]["latency_ms"] == 1235  # rounded
    assert recs[0]["total_tokens"] == 100


def test_roundtrip_preserves_types_not_just_values():
    """The whole reason the store JSON-encodes params instead of str()-ing them."""
    llmops.record(
        "/chat", 100.0,
        session_id="s1", model="m",
        prompt_tokens=10, completion_tokens=5, total_tokens=15,
        tool_calls=["read_brief", "estimate_cost_and_timeline"],
        is_grounded=False, confidence=0.82,
    )
    r = llmops.read_records()[0]
    assert r["is_grounded"] is False              # not the string "False", which is truthy
    assert r["tool_calls"] == ["read_brief", "estimate_cost_and_timeline"]  # a list, not "a,b"
    assert isinstance(r["total_tokens"], int)     # not 15.0 — metrics come back as floats
    assert isinstance(r["confidence"], float)
    assert r["confidence"] == pytest.approx(0.82)


def test_grounded_rate_survives_the_roundtrip():
    """End-to-end guard on the truthy-'False' bug: through the store, not a literal."""
    llmops.record("/chat", 1.0, is_grounded=True)
    llmops.record("/chat", 1.0, is_grounded=False)
    agg = metrics.aggregate(llmops.read_records())
    assert agg["grounded_rate"] == pytest.approx(0.5)


def test_absent_measurements_stay_absent():
    """A blocked turn has no tokens. None must not become 0 (drags the mean) or "None"."""
    llmops.record("/chat", 50.0, guardrail_reason="blocked: off-topic")
    r = llmops.read_records()[0]
    assert r["total_tokens"] is None
    assert r["error"] is None
    assert r["model"] is None
    assert r["tool_calls"] == []
    assert r["guardrail_reason"] == "blocked: off-topic"


def test_records_come_back_oldest_first():
    """traces.load_sessions sorts by ts, but /metrics slices recent[-50:][::-1]."""
    for i in range(5):
        llmops.record("/chat", float(i), session_id=f"s{i}")
    assert [r["session_id"] for r in llmops.read_records()] == ["s0", "s1", "s2", "s3", "s4"]


def test_read_records_limit_returns_the_most_recent():
    for i in range(5):
        llmops.record("/chat", float(i), session_id=f"s{i}")
    assert [r["session_id"] for r in llmops.read_records(limit=2)] == ["s3", "s4"]


def test_disabled_is_noop(monkeypatch):
    monkeypatch.setenv("LLMOPS_ENABLED", "0")
    llmops.record("/chat", 10.0)
    assert llmops.read_records() == []


def test_store_failure_never_crashes_a_request(monkeypatch):
    """Observability must not take a request down with it."""
    def boom(*_a, **_k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(sink, "_client", boom)
    llmops.record("/chat", 10.0)  # must not raise


def test_concurrent_append(log_isolated_experiment):
    """8 threads, 20 records each — every record must survive."""
    def worker(i: int):
        for _ in range(20):
            llmops.record("/chat", float(i), total_tokens=i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(llmops.read_records()) == 8 * 20


def test_record_carries_guardrail_reason():
    llmops.record(
        "/chat", 10.0,
        is_grounded=True, confidence=0.9,
        guardrail_reason="blocked: off-topic",
    )
    r = llmops.read_records()[0]
    assert r["guardrail_reason"] == "blocked: off-topic"


# ── metrics (pure, no store) ────────────────────────────────────────────────


def test_aggregate():
    recs = [
        {"latency_ms": 100, "total_tokens": 10, "error": None},
        {"latency_ms": 200, "total_tokens": 30, "error": None},
        {"latency_ms": 300, "total_tokens": None, "error": "Boom"},
    ]
    agg = metrics.aggregate(recs)
    assert agg["count"] == 3
    assert agg["latency_p50_ms"] == 200
    assert agg["total_tokens"] == 40
    assert agg["avg_tokens"] == 20  # only the 2 non-null token rows
    assert agg["error_rate"] == pytest.approx(1 / 3)


def test_aggregate_empty():
    agg = metrics.aggregate([])
    assert agg["count"] == 0
    assert agg["latency_p50_ms"] is None
    assert agg["error_rate"] == 0.0


def test_aggregate_quality_signals():
    recs = [
        {"latency_ms": 1, "is_grounded": True, "confidence": 0.8, "guardrail_reason": None},
        {"latency_ms": 1, "is_grounded": False, "confidence": 0.4, "guardrail_reason": "blocked"},
        {"latency_ms": 1, "is_grounded": None, "confidence": None, "guardrail_reason": None},
    ]
    agg = metrics.aggregate(recs)
    assert agg["grounded_rate"] == pytest.approx(0.5)      # 1 of 2 non-null
    assert agg["mean_confidence"] == pytest.approx(0.6)     # (0.8+0.4)/2
    assert agg["guardrail_fire_rate"] == pytest.approx(1 / 3)  # 1 of 3 rows


# ── usage accumulator (pure, no store) ──────────────────────────────────────


def test_usage_accumulates_across_calls():
    """Multiple LLM calls in one request (loop turns + nested RAG) sum to one total."""
    usage.reset_usage()
    usage.add_usage({"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120})
    usage.add_usage({"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60})
    total = usage.pop_usage()
    assert total == {"prompt_tokens": 150, "completion_tokens": 30, "total_tokens": 180}
    assert usage.pop_usage() is None  # drained


def test_usage_none_when_never_seen():
    usage.reset_usage()
    assert usage.pop_usage() is None  # no LLM call recorded any usage
