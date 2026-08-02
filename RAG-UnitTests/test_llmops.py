"""LLMOps sink + metrics + usage-accumulator self-checks (docs/plan-llmops.md).

No network, no LLM — pure JSONL + aggregation + thread-local logic.
"""

from __future__ import annotations

import threading

import pytest

from stratpoint_rag import llmops
from stratpoint_rag.llmops import metrics, sink, usage


@pytest.fixture
def log_path(tmp_path, monkeypatch):
    p = tmp_path / "traces.jsonl"
    monkeypatch.setenv("LLMOPS_LOG_PATH", str(p))
    monkeypatch.setenv("LLMOPS_ENABLED", "1")
    return p


def test_record_roundtrip(log_path):
    llmops.record("/chat", 1234.7, model="m", total_tokens=100, is_grounded=True)
    recs = llmops.read_records()
    assert len(recs) == 1
    assert recs[0]["path"] == "/chat"
    assert recs[0]["latency_ms"] == 1235  # rounded
    assert recs[0]["total_tokens"] == 100


def test_disabled_is_noop(log_path, monkeypatch):
    monkeypatch.setenv("LLMOPS_ENABLED", "0")
    llmops.record("/chat", 10.0)
    assert llmops.read_records() == []


def test_concurrent_append_no_interleaving(log_path):
    """Every line must be valid JSON even under concurrent writers (the lock's job)."""
    def worker(i: int):
        for _ in range(20):
            llmops.record("/chat", float(i), total_tokens=i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    recs = llmops.read_records()  # raises/loses lines if any write interleaved
    assert len(recs) == 8 * 20


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


def test_read_skips_corrupt_line(log_path):
    log_path.write_text('{"latency_ms": 1}\nnot json\n{"latency_ms": 2}\n')
    recs = sink.read_records()
    assert [r["latency_ms"] for r in recs] == [1, 2]


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
