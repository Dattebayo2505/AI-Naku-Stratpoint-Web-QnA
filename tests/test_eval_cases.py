"""Gold-set parsing and validation.

The gold file is hand-authored data that three later roadmap sessions depend
on. A typo in a slug, a retrieve case missing its gold page, or an abstain case
that accidentally carries one would all silently corrupt the numbers rather
than fail, so loading validates hard and names every offender.
"""
import json

import pytest

from stratpoint_rag.rag.eval.run import GoldCase, GoldSetError, load_cases


def write_gold(tmp_path, rows):
    p = tmp_path / "gold.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return p


class TestLoadCases:
    def test_parses_a_retrieve_case(self, tmp_path):
        p = write_gold(tmp_path, [
            {"id": "svc-01", "q": "Does Stratpoint offer OutSystems?",
             "slug": "outsystems-offerings", "expect": "retrieve", "axis": "entity-named"},
        ])
        assert load_cases(p) == [
            GoldCase(id="svc-01", q="Does Stratpoint offer OutSystems?",
                     expect="retrieve", slug="outsystems-offerings",
                     axis="entity-named", paraphrase_of=None)
        ]

    def test_parses_an_abstain_case_with_no_slug(self, tmp_path):
        p = write_gold(tmp_path, [
            {"id": "abs-headcount", "q": "How many employees?", "expect": "abstain"},
        ])
        case = load_cases(p)[0]
        assert case.slug is None and case.expect == "abstain"

    def test_carries_paraphrase_of(self, tmp_path):
        p = write_gold(tmp_path, [
            {"id": "svc-01", "q": "a", "slug": "s", "expect": "retrieve", "axis": "entity-named"},
            {"id": "svc-01p", "q": "b", "slug": "s", "expect": "retrieve",
             "axis": "pronoun", "paraphrase_of": "svc-01"},
        ])
        assert load_cases(p)[1].paraphrase_of == "svc-01"

    def test_blank_lines_are_skipped(self, tmp_path):
        p = tmp_path / "gold.jsonl"
        p.write_text('\n{"id":"a","q":"q","slug":"s","expect":"retrieve","axis":"entity-named"}\n\n',
                     encoding="utf-8")
        assert len(load_cases(p)) == 1


class TestValidation:
    def test_retrieve_case_without_slug_is_rejected(self, tmp_path):
        p = write_gold(tmp_path, [{"id": "x", "q": "q", "expect": "retrieve"}])
        with pytest.raises(GoldSetError, match="x"):
            load_cases(p)

    def test_abstain_case_with_slug_is_rejected(self, tmp_path):
        p = write_gold(tmp_path, [{"id": "x", "q": "q", "expect": "abstain", "slug": "s"}])
        with pytest.raises(GoldSetError, match="x"):
            load_cases(p)

    def test_unknown_expect_value_is_rejected(self, tmp_path):
        p = write_gold(tmp_path, [{"id": "x", "q": "q", "expect": "maybe"}])
        with pytest.raises(GoldSetError, match="maybe"):
            load_cases(p)

    def test_duplicate_ids_are_rejected(self, tmp_path):
        p = write_gold(tmp_path, [
            {"id": "dup", "q": "a", "slug": "s", "expect": "retrieve", "axis": "entity-named"},
            {"id": "dup", "q": "b", "slug": "s", "expect": "retrieve", "axis": "entity-named"},
        ])
        with pytest.raises(GoldSetError, match="dup"):
            load_cases(p)

    def test_malformed_json_names_the_line_number(self, tmp_path):
        p = tmp_path / "gold.jsonl"
        p.write_text('{"id":"ok","q":"q","slug":"s","expect":"retrieve","axis":"entity-named"}\n{oops\n',
                     encoding="utf-8")
        with pytest.raises(GoldSetError, match="line 2"):
            load_cases(p)

    def test_slug_absent_from_corpus_is_rejected_and_listed(self, tmp_path):
        p = write_gold(tmp_path, [
            {"id": "a", "q": "q", "slug": "real-page", "expect": "retrieve", "axis": "entity-named"},
            {"id": "b", "q": "q", "slug": "ghost-page", "expect": "retrieve", "axis": "entity-named"},
        ])
        with pytest.raises(GoldSetError, match="ghost-page"):
            load_cases(p, known_slugs={"real-page"})

    def test_known_slugs_none_skips_the_corpus_check(self, tmp_path):
        p = write_gold(tmp_path, [
            {"id": "a", "q": "q", "slug": "anything", "expect": "retrieve", "axis": "entity-named"},
        ])
        assert len(load_cases(p, known_slugs=None)) == 1

    def test_paraphrase_of_pointing_at_unknown_id_is_rejected(self, tmp_path):
        p = write_gold(tmp_path, [
            {"id": "b", "q": "q", "slug": "s", "expect": "retrieve",
             "axis": "pronoun", "paraphrase_of": "missing"},
        ])
        with pytest.raises(GoldSetError, match="missing"):
            load_cases(p)
