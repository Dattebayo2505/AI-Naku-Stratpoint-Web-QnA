"""Baseline persistence and staleness detection.

Roadmap Sessions 1 and 3 both invalidate the baseline — corpus hygiene changes
which chunks exist, the BGE query prefix shifts every score. A diff taken
across that boundary compares incompatible indexes and looks like a regression.
The fingerprint is what makes the roadmap's "#3 before #1, never together" rule
enforceable rather than advisory.
"""
import json

from stratpoint_rag.rag.eval.run import (
    CaseResult,
    baseline_is_stale,
    corpus_fingerprint,
    diff_baseline,
    load_baseline,
    write_baseline,
)


def res(cid, rank, gold, top1=0.9):
    return CaseResult(id=cid, expect="retrieve", axis="entity-named",
                      rank=rank, gold_score=gold, top1_score=top1)


class TestCorpusFingerprint:
    def test_is_stable_across_calls(self, tmp_path):
        p = tmp_path / "index.jsonl"
        p.write_text(
            '{"slug":"a","content_hash":"h1","status":"ok"}\n'
            '{"slug":"b","content_hash":"h2","status":"ok"}\n', encoding="utf-8")
        assert corpus_fingerprint(p) == corpus_fingerprint(p)

    def test_changes_when_a_page_changes(self, tmp_path):
        p = tmp_path / "index.jsonl"
        p.write_text('{"slug":"a","content_hash":"h1","status":"ok"}\n', encoding="utf-8")
        before = corpus_fingerprint(p)
        p.write_text('{"slug":"a","content_hash":"h2","status":"ok"}\n', encoding="utf-8")
        assert corpus_fingerprint(p) != before

    def test_changes_when_a_page_is_removed(self, tmp_path):
        """Session 1 removes the -pdf twins; the baseline must notice."""
        p = tmp_path / "index.jsonl"
        p.write_text('{"slug":"a","content_hash":"h1","status":"ok"}\n'
                     '{"slug":"a-pdf","content_hash":"h1","status":"ok"}\n', encoding="utf-8")
        before = corpus_fingerprint(p)
        p.write_text('{"slug":"a","content_hash":"h1","status":"ok"}\n', encoding="utf-8")
        assert corpus_fingerprint(p) != before

    def test_skipped_pages_count_as_present(self, tmp_path):
        """Corpus invariant: a page is present when status is ok OR skipped."""
        p = tmp_path / "index.jsonl"
        p.write_text('{"slug":"a","content_hash":"h1","status":"skipped"}\n'
                     '{"slug":"b","content_hash":"h2","status":"failed"}\n', encoding="utf-8")
        assert corpus_fingerprint(p).startswith("1:")


class TestRoundTrip:
    def test_written_baseline_loads_back(self, tmp_path):
        p = tmp_path / "baseline.json"
        write_baseline(p, [res("a", 0, 0.81)], embed_model="bge-small", fingerprint="1:abc")
        b = load_baseline(p)
        assert b["embed_model"] == "bge-small"
        assert b["fingerprint"] == "1:abc"
        assert b["cases"]["a"]["rank"] == 0
        assert b["cases"]["a"]["gold_score"] == 0.81

    def test_file_is_human_readable_json(self, tmp_path):
        p = tmp_path / "baseline.json"
        write_baseline(p, [res("a", 0, 0.81)], embed_model="m", fingerprint="f")
        assert json.loads(p.read_text(encoding="utf-8"))["cases"]["a"]["top1_score"] == 0.9


class TestStaleness:
    def test_matching_model_and_fingerprint_is_fresh(self):
        b = {"embed_model": "m", "fingerprint": "f", "cases": {}}
        assert baseline_is_stale(b, "m", "f") is None

    def test_changed_fingerprint_is_reported(self):
        b = {"embed_model": "m", "fingerprint": "old", "cases": {}}
        assert "corpus" in baseline_is_stale(b, "m", "new")

    def test_changed_embed_model_is_reported(self):
        b = {"embed_model": "old", "fingerprint": "f", "cases": {}}
        assert "embedding model" in baseline_is_stale(b, "new", "f")


class TestDiff:
    def test_reports_rank_and_score_movement(self):
        b = {"cases": {"a": {"rank": 2, "gold_score": 0.60, "top1_score": 0.70}}}
        d = diff_baseline([res("a", 0, 0.81, top1=0.81)], b)[0]
        assert (d.rank_before, d.rank_after) == (2, 0)
        assert (d.score_before, d.score_after) == (0.60, 0.81)

    def test_new_case_absent_from_baseline_has_none_before(self):
        d = diff_baseline([res("new", 0, 0.8)], {"cases": {}})[0]
        assert d.rank_before is None and d.rank_after == 0

    def test_case_dropped_from_the_gold_set_is_not_reported(self):
        """The current run defines the case list; stale baseline rows are ignored."""
        b = {"cases": {"gone": {"rank": 1, "gold_score": 0.5, "top1_score": 0.5},
                       "a": {"rank": 1, "gold_score": 0.5, "top1_score": 0.5}}}
        assert [d.id for d in diff_baseline([res("a", 0, 0.8)], b)] == ["a"]
