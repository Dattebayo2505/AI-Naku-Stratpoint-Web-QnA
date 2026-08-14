from __future__ import annotations

from stratpoint_rag.evaluation import harness as h


def test_layer_result_pass_rate():
    r = h.LayerResult("unit", "guardrails/deterministic", total=20, passed=18)
    assert r.pass_rate == 0.9


def test_below_floor_uses_get_default_one():
    # A layer with no FLOORS entry must fail loud (floor defaults to 1.0).
    r = h.LayerResult("x", "not/registered", total=10, passed=9)
    assert h.below_floor(r) is True


def test_below_floor_respects_registered_floor():
    r = h.LayerResult("unit", "guardrails/deterministic", total=20, passed=18)
    # deterministic floor is 0.60 (measured baseline 13/20 = 0.65); 0.90 clears it
    assert h.below_floor(r) is False


def test_skipped_layer_never_below_floor():
    r = h.LayerResult("live", "guardrails/end-to-end", total=0, passed=0, skipped=True)
    assert h.below_floor(r) is False


def test_format_table_contains_layer_names():
    rows = [h.LayerResult("unit", "guardrails/deterministic", 20, 18)]
    out = h.format_table(rows)
    assert "guardrails/deterministic" in out
    assert "18" in out and "20" in out


def test_end_to_end_layer_skips_when_nemo_unavailable(monkeypatch):
    # live_available() gates on NVIDIA_API_KEY *and* nemoguardrails being
    # importable (guardrail_agent swallows the ImportError otherwise, which
    # would silently report deterministic numbers under a NeMo label). When
    # it's False for any reason, the layer must skip rather than run and
    # must never be able to fail the command.
    monkeypatch.setattr(h.ge, "live_available", lambda: False)
    result = h._guardrail_end_to_end()
    assert result.skipped is True
    assert h.below_floor(result) is False
