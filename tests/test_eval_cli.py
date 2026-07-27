"""Report formatting and the CLI's failure messages.

The eval is run by hand between roadmap sessions, so its output has to be
readable without opening the source, and its failure modes have to point at the
fix (build the index) rather than at a stack trace.
"""
import pytest

from stratpoint_rag.rag.eval.run import (
    CaseResult,
    GoldCase,
    Separation,
    format_report,
)


def gold_case(cid, axis="entity-named", paraphrase_of=None):
    return GoldCase(id=cid, q="q", expect="retrieve", slug="p", axis=axis,
                    paraphrase_of=paraphrase_of)


def res(cid, rank, gold, top1, expect="retrieve", axis="entity-named"):
    return CaseResult(id=cid, expect=expect, axis=axis, rank=rank,
                      gold_score=gold, top1_score=top1)


SEP = Separation(gold_min=0.70, gold_p25=0.74, gold_median=0.78,
                 abstain_median=0.55, abstain_p75=0.58, abstain_max=0.60,
                 overlap_count=0, abstain_total=8, gold_total=20)


class TestFormatReport:
    def test_shows_hit_and_miss_per_case_with_scores(self):
        out = format_report([res("a", 0, 0.81, 0.81)], [gold_case("a")], SEP, k=5)
        assert "HIT" in out and "a" in out and "0.81" in out

    def test_marks_a_miss(self):
        out = format_report([res("b", None, None, 0.55)], [gold_case("b")], SEP, k=5)
        assert "MISS" in out

    def test_reports_per_axis_hit_rate(self):
        results = [res("a", 0, 0.8, 0.8, axis="entity-named"),
                   res("ap", None, None, 0.5, axis="pronoun")]
        cases = [gold_case("a"), gold_case("ap", axis="pronoun", paraphrase_of="a")]
        out = format_report(results, cases, SEP, k=5)
        assert "pronoun" in out and "entity-named" in out

    def test_flags_divergent_paraphrase_pairs(self):
        results = [res("a", 0, 0.8, 0.8), res("ap", None, None, 0.5, axis="pronoun")]
        cases = [gold_case("a"), gold_case("ap", axis="pronoun", paraphrase_of="a")]
        assert "a -> ap" in format_report(results, cases, SEP, k=5)

    def test_separation_verdict_is_positive_when_no_overlap(self):
        out = format_report([res("a", 0, 0.8, 0.8)], [gold_case("a")], SEP, k=5)
        assert "0/8" in out

    def test_separation_verdict_warns_when_distributions_overlap(self):
        overlapping = Separation(gold_min=0.5, gold_p25=0.6, gold_median=0.65,
                                 abstain_median=0.66, abstain_p75=0.70, abstain_max=0.75,
                                 overlap_count=6, abstain_total=8, gold_total=20)
        out = format_report([res("a", 0, 0.8, 0.8)], [gold_case("a")], overlapping, k=5)
        assert "6/8" in out
        assert "overlap" in out.lower()


class TestMainErrors:
    def test_empty_store_message_points_at_ingest(self, monkeypatch, tmp_path):
        """An unbuilt index is the most likely first-run failure; it must name
        the command that fixes it rather than surfacing a Chroma traceback."""
        from stratpoint_rag.rag.eval import run as mod

        # An abstain-only gold file: no slug, so validation cannot fail on the
        # corpus check and this test stays independent of data/index.jsonl.
        gold = tmp_path / "gold.jsonl"
        gold.write_text('{"id":"a1","q":"How many employees?","expect":"abstain"}\n',
                        encoding="utf-8")

        def boom():
            raise RuntimeError("nothing indexed")

        monkeypatch.setattr(mod, "_build_retrieve_fn", boom)
        with pytest.raises(SystemExit) as exc:
            mod.main(["--gold", str(gold)])
        assert "stratpoint-rag-ingest" in str(exc.value)

    def test_invalid_gold_set_fails_before_touching_retrieval(self, tmp_path):
        from stratpoint_rag.rag.eval import run as mod

        gold = tmp_path / "gold.jsonl"
        gold.write_text('{"id":"bad","q":"q","expect":"retrieve"}\n', encoding="utf-8")
        with pytest.raises(SystemExit) as exc:
            mod.main(["--gold", str(gold)])
        assert "gold set is invalid" in str(exc.value)
