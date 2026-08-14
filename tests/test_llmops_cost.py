from __future__ import annotations

import pytest

from stratpoint_rag.llmops import cost, metrics

def test_estimate_cost_math(monkeypatch):
    # Pin the DEFAULT rates regardless of a .env override (rag/config.py calls
    # load_dotenv() at import, so LLMOPS_PRICE_PER_1K_PROMPT/_COMPLETION in
    # .env would otherwise leak into this process's environment).
    monkeypatch.delenv("LLMOPS_PRICE_PER_1K_PROMPT", raising=False)
    monkeypatch.delenv("LLMOPS_PRICE_PER_1K_COMPLETION", raising=False)
    # 1000 prompt + 1000 completion at the cited median rates
    c = cost.estimate_cost(1000, 1000)
    assert c == pytest.approx(0.00008 + 0.00009)

def test_estimate_cost_all_none_is_none():
    assert cost.estimate_cost(None, None) is None

def test_estimate_cost_partial_counts_the_known_side(monkeypatch):
    monkeypatch.delenv("LLMOPS_PRICE_PER_1K_PROMPT", raising=False)
    monkeypatch.delenv("LLMOPS_PRICE_PER_1K_COMPLETION", raising=False)
    assert cost.estimate_cost(1000, None) == pytest.approx(0.00008)

def test_estimate_cost_prompt_env_override_applied(monkeypatch):
    monkeypatch.setenv("LLMOPS_PRICE_PER_1K_PROMPT", "0.001")
    monkeypatch.delenv("LLMOPS_PRICE_PER_1K_COMPLETION", raising=False)
    c = cost.estimate_cost(1000, None)
    assert c == pytest.approx(0.001)

def test_estimate_cost_malformed_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("LLMOPS_PRICE_PER_1K_PROMPT", "garbage")
    c = cost.estimate_cost(1000, None)
    assert c == pytest.approx(0.00008)

def test_aggregate_sums_cost():
    recs = [
        {"latency_ms": 1, "cost_usd": 0.001},
        {"latency_ms": 1, "cost_usd": 0.002},
        {"latency_ms": 1, "cost_usd": None},
    ]
    assert metrics.aggregate(recs)["total_cost_usd"] == pytest.approx(0.003)
