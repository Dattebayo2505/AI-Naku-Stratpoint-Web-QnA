"""Live eval against the built Chroma store.

Marked integration: it needs a populated chroma_db/, which is gitignored and
regenerated from data/. Build it with `uv run stratpoint-rag-ingest` first.
"""
import pytest

from stratpoint_rag.rag.eval.run import (
    GOLD,
    _build_retrieve_fn,
    _known_slugs,
    hit_rate,
    load_cases,
    run_cases,
    separation,
)


@pytest.mark.integration
class TestLiveEval:
    def test_gold_set_loads_against_the_real_corpus(self):
        cases = load_cases(GOLD, known_slugs=_known_slugs())
        assert len(cases) >= 35
        assert any(c.expect == "abstain" for c in cases)
        assert any(c.paraphrase_of for c in cases)

    def test_eval_runs_end_to_end_and_produces_scores(self):
        cases = load_cases(GOLD, known_slugs=_known_slugs())
        results = run_cases(cases, _build_retrieve_fn(), k=5)
        assert len(results) == len(cases)
        assert any(r.top1_score is not None for r in results)

    def test_abstention_cases_retrieve_something_and_are_scored(self):
        """If these returned nothing, the separation report would be vacuous."""
        cases = [c for c in load_cases(GOLD, known_slugs=_known_slugs())
                 if c.expect == "abstain"]
        results = run_cases(cases, _build_retrieve_fn(), k=5)
        assert all(r.top1_score is not None for r in results)

    def test_baseline_hit_rate_is_recorded_not_asserted(self):
        """Deliberately a floor, not a target: this guards catastrophic breakage
        without pinning the number the later sessions exist to improve."""
        cases = load_cases(GOLD, known_slugs=_known_slugs())
        results = run_cases(cases, _build_retrieve_fn(), k=5)
        assert hit_rate(results, k=5) > 0.3
