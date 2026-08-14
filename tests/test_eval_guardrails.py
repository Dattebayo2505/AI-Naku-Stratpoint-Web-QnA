from __future__ import annotations

from stratpoint_rag.evaluation import guardrail_eval as ge


def test_loads_twenty_cases():
    cases = ge.load_cases()
    assert len(cases) == 20
    assert {c.expected for c in cases} <= {"block", "redact", "allow"}
    assert all(c.id and c.input and c.category for c in cases)


def test_deterministic_runner_shape_and_baseline():
    res = ge.run_guardrail_eval(use_nemo=False)
    assert set(res) == {"total", "passed", "pass_rate", "use_nemo", "by_category", "failures"}
    assert res["total"] == 20
    assert res["use_nemo"] is False
    # Deterministic (regex-only) baseline: injection 2/4, pii 3/3 (redact is
    # the correct, production-policy outcome for PII — see D3 in
    # guardrail_eval.py), offtopic 0/5 (the documented gap NeMo closes),
    # benign 8/8 — expected honest total is 13/20. Floor is one below that so
    # a small regex improvement or a single flake doesn't turn this red.
    assert res["passed"] >= 12
    assert res["pass_rate"] == res["passed"] / res["total"]


def test_failures_carry_case_ids():
    res = ge.run_guardrail_eval(use_nemo=False)
    for f in res["failures"]:
        assert "id" in f and "expected" in f and "got" in f
