"""Per-case scoring and aggregate metrics, with retrieval faked out.

These are the numbers Sessions 1, 3 and 4 of the retrieval roadmap read, so
they are tested against hand-built chunk lists rather than a live index: the
arithmetic has to be verifiable without a 371-page corpus in the loop.
"""
import pytest

from stratpoint_rag.rag.eval.run import (
    CaseResult,
    GoldCase,
    evaluate_case,
    run_cases,
)
from stratpoint_rag.rag.models import Chunk


def chunk(slug, score):
    return Chunk(id="", slug=slug, url=f"https://x/{slug}", title=slug, text="", score=score)


def retrieve_case(cid="c1", slug="target", axis="entity-named"):
    return GoldCase(id=cid, q="q", expect="retrieve", slug=slug, axis=axis)


class TestEvaluateCase:
    def test_gold_at_rank_zero(self):
        r = evaluate_case(retrieve_case(), [chunk("target", 0.81), chunk("other", 0.70)])
        assert (r.rank, r.gold_score, r.top1_score) == (0, 0.81, 0.81)

    def test_gold_further_down_keeps_both_scores(self):
        r = evaluate_case(retrieve_case(), [chunk("other", 0.79), chunk("target", 0.62)])
        assert (r.rank, r.gold_score, r.top1_score) == (1, 0.62, 0.79)

    def test_gold_absent_leaves_rank_and_gold_score_none(self):
        r = evaluate_case(retrieve_case(), [chunk("other", 0.60), chunk("more", 0.55)])
        assert r.rank is None and r.gold_score is None and r.top1_score == 0.60

    def test_duplicate_gold_slug_takes_the_best_rank(self):
        """Chunking splits a page, so the same slug legitimately appears twice."""
        r = evaluate_case(retrieve_case(), [chunk("target", 0.80), chunk("target", 0.71)])
        assert r.rank == 0 and r.gold_score == 0.80

    def test_abstain_case_records_only_top1(self):
        r = evaluate_case(GoldCase(id="a1", q="q", expect="abstain"), [chunk("junk", 0.58)])
        assert r.rank is None and r.gold_score is None and r.top1_score == 0.58

    def test_empty_retrieval_yields_all_none(self):
        r = evaluate_case(retrieve_case(), [])
        assert r.rank is None and r.gold_score is None and r.top1_score is None

    def test_axis_and_expect_are_carried_through(self):
        r = evaluate_case(retrieve_case(axis="pronoun"), [chunk("target", 0.7)])
        assert r.axis == "pronoun" and r.expect == "retrieve"


class TestRunCases:
    def test_passes_query_and_k_to_retrieve_fn(self):
        seen = []

        def fake(q, k):
            seen.append((q, k))
            return [chunk("target", 0.9)]

        run_cases([retrieve_case()], fake, k=8)
        assert seen == [("q", 8)]

    def test_returns_one_result_per_case_in_order(self):
        cases = [retrieve_case("a"), retrieve_case("b")]
        results = run_cases(cases, lambda q, k: [chunk("target", 0.9)])
        assert [r.id for r in results] == ["a", "b"]
