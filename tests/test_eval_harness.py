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


def test_the_nemo_end_to_end_layer_is_not_registered():
    """Deregistered 2026-08-14.

    NeMo has never executed in this repo. `LLMRails.check()` hardcodes
    `options["log"] = {"activated_rails": True}`, which Colang 2.0 rejects, and
    both the 2.x config and the check() call landed together in the original
    integration (2026-07-05). The layer could therefore only ever report SKIP,
    and a permanently skipped row invites the reader to assume the comparison
    was made. Removing it is the honest table; the capability itself is
    unaffected — `run_guardrail_eval(use_nemo=True)` still exists for whoever
    resolves the Colang incompatibility.
    """
    assert not any(fn.__name__ == "_guardrail_end_to_end" for fn in h.REGISTRY)
    assert "guardrails/end-to-end" not in h.FLOORS
