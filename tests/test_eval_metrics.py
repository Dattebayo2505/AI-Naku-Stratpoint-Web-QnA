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


from stratpoint_rag.rag.eval.run import (
    divergent_pairs,
    hit_rate,
    hit_rate_by_axis,
    mrr,
)


def result(cid, rank, *, expect="retrieve", axis="entity-named", gold=0.8, top1=0.8):
    return CaseResult(id=cid, expect=expect, axis=axis, rank=rank,
                      gold_score=gold if rank is not None else None, top1_score=top1)


class TestHitRate:
    def test_counts_only_ranks_inside_k(self):
        results = [result("a", 0), result("b", 4), result("c", 7), result("d", None)]
        assert hit_rate(results, k=5) == 0.5

    def test_abstain_cases_are_excluded(self):
        results = [result("a", 0), result("z", None, expect="abstain", axis=None)]
        assert hit_rate(results, k=5) == 1.0

    def test_no_retrieve_cases_is_zero_not_a_crash(self):
        assert hit_rate([result("z", None, expect="abstain", axis=None)], k=5) == 0.0


class TestHitRateByAxis:
    def test_splits_by_axis(self):
        results = [
            result("a", 0, axis="entity-named"),
            result("b", 1, axis="entity-named"),
            result("ap", 0, axis="pronoun"),
            result("bp", None, axis="pronoun"),
        ]
        assert hit_rate_by_axis(results, k=5) == {"entity-named": 1.0, "pronoun": 0.5}


class TestMRR:
    def test_reciprocal_of_one_based_rank(self):
        # rank 0 -> 1/1, rank 1 -> 1/2, miss -> 0  =>  (1 + 0.5 + 0) / 3
        results = [result("a", 0), result("b", 1), result("c", None)]
        assert mrr(results, k=5) == pytest.approx(0.5)

    def test_rank_beyond_k_scores_zero(self):
        assert mrr([result("a", 9)], k=5) == 0.0


class TestDivergentPairs:
    def test_reports_twin_missing_while_sibling_hits(self):
        """The regression guard for the anchor_entity fix in a7cb482."""
        cases = [
            GoldCase(id="s1", q="q", expect="retrieve", slug="p", axis="entity-named"),
            GoldCase(id="s1p", q="q", expect="retrieve", slug="p", axis="pronoun",
                     paraphrase_of="s1"),
        ]
        results = [result("s1", 0), result("s1p", None)]
        assert divergent_pairs(results, cases) == [("s1", "s1p")]

    def test_silent_when_both_hit(self):
        cases = [
            GoldCase(id="s1", q="q", expect="retrieve", slug="p", axis="entity-named"),
            GoldCase(id="s1p", q="q", expect="retrieve", slug="p", axis="pronoun",
                     paraphrase_of="s1"),
        ]
        assert divergent_pairs([result("s1", 0), result("s1p", 2)], cases) == []

    def test_silent_when_both_miss(self):
        """Both missing is a coverage problem, not an anchoring regression."""
        cases = [
            GoldCase(id="s1", q="q", expect="retrieve", slug="p", axis="entity-named"),
            GoldCase(id="s1p", q="q", expect="retrieve", slug="p", axis="pronoun",
                     paraphrase_of="s1"),
        ]
        assert divergent_pairs([result("s1", None), result("s1p", None)], cases) == []
