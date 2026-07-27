"""The separation report — Session 4's go/no-go.

A relevance floor is a single global cutoff. It can only work if the scores of
correct chunks on answerable questions sit above the top-1 scores on questions
the corpus cannot answer. `overlap_count` measures how badly those two
distributions interleave; a high count means no cutoff separates them and the
floor needs redesigning before it is written.
"""
import pytest

from stratpoint_rag.rag.eval.run import CaseResult, separation


def gold(cid, score, rank=0):
    return CaseResult(id=cid, expect="retrieve", axis="entity-named",
                      rank=rank, gold_score=score, top1_score=score)


def abstain(cid, top1):
    return CaseResult(id=cid, expect="abstain", axis=None,
                      rank=None, gold_score=None, top1_score=top1)


class TestSeparation:
    def test_clean_separation_reports_no_overlap(self):
        results = [gold("g1", 0.80), gold("g2", 0.78), gold("g3", 0.76),
                   abstain("a1", 0.55), abstain("a2", 0.58)]
        s = separation(results)
        assert s.overlap_count == 0
        assert s.gold_median == pytest.approx(0.78)
        assert s.abstain_max == pytest.approx(0.58)

    def test_counts_abstain_cases_above_the_gold_median(self):
        results = [gold("g1", 0.60), gold("g2", 0.70), gold("g3", 0.80),
                   abstain("a1", 0.75), abstain("a2", 0.72), abstain("a3", 0.40)]
        s = separation(results)
        assert s.gold_median == pytest.approx(0.70)
        assert s.overlap_count == 2      # 0.75 and 0.72 both exceed 0.70
        assert s.abstain_total == 3

    def test_missed_answerable_cases_do_not_contribute_a_gold_score(self):
        """A miss has no gold chunk, so it cannot inform the gold distribution."""
        results = [gold("g1", 0.80),
                   CaseResult(id="g2", expect="retrieve", axis="entity-named",
                              rank=None, gold_score=None, top1_score=0.9),
                   abstain("a1", 0.5)]
        s = separation(results)
        assert s.gold_total == 1 and s.gold_median == pytest.approx(0.80)

    def test_rank_outside_k_does_not_count_as_a_gold_score(self):
        results = [gold("g1", 0.80), gold("g2", 0.30, rank=9), abstain("a1", 0.5)]
        s = separation(results, k=5)
        assert s.gold_total == 1

    def test_no_abstain_cases_yields_zero_overlap_and_none_stats(self):
        s = separation([gold("g1", 0.8)])
        assert s.overlap_count == 0 and s.abstain_total == 0 and s.abstain_max is None

    def test_no_gold_hits_yields_none_stats_and_zero_overlap(self):
        s = separation([abstain("a1", 0.5)])
        assert s.gold_median is None and s.overlap_count == 0
