"""seed_cases — the generator both new eval layers read.

Only the offline halves are tested here: turning a PDF into the brief's content
words, and turning a written case file back into scorable quotes. Driving the
live chain over HTTP is the same shape as seed_traces and is not unit-tested.
"""

from __future__ import annotations

import json

import pymupdf as fitz

from stratpoint_rag.evaluation import seed_cases as sc


def _pdf(text: str) -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text, fontsize=11)
    return doc.tobytes()


def _estimation(**overrides) -> dict:
    est = {
        "total_cost_usd": 7000.0,
        "currency_code": "USD",
        "estimated_weeks": 12.0,
        "role_breakdown": [
            {"role": "Senior Dev", "estimated_hours": 60.0,
             "hourly_rate": 100.0, "total_cost": 6000.0},
            {"role": "QA", "estimated_hours": 20.0,
             "hourly_rate": 50.0, "total_cost": 1000.0},
        ],
        "phase_timeline": [],
        "summary": "Twelve weeks across two roles.",
    }
    est.update(overrides)
    return est


def test_brief_words_come_from_the_pdf_text_layer(tmp_path):
    path = tmp_path / "brief.pdf"
    path.write_bytes(_pdf("Android catalog with PCI compliant payments"))

    words = sc.brief_words(path)

    assert "android" in words
    assert "catalog" in words
    assert "okta" not in words


def test_brief_words_are_serialisable(tmp_path):
    """The case file is JSON, so the word set must survive a round trip."""
    path = tmp_path / "brief.pdf"
    path.write_bytes(_pdf("Android catalog"))

    words = sc.brief_words(path)

    assert json.loads(json.dumps(sorted(words))) == sorted(words)


def test_load_quote_cases_rebuilds_a_scorable_context(tmp_path):
    path = tmp_path / "pipeline_runs.jsonl"
    path.write_text(
        json.dumps({"file": "rfp1.pdf", "requirements": {}, "brief_words": [],
                    "estimation": _estimation()}) + "\n",
        encoding="utf-8",
    )

    cases = sc.load_quote_cases(path)

    assert len(cases) == 1
    assert cases[0]["file"] == "rfp1.pdf"
    assert cases[0]["declared_currency"] == "USD"
    assert cases[0]["context"].subtotal_amount > 0


def test_a_case_with_no_estimation_is_skipped_not_crashed(tmp_path):
    """A stalled run has requirements but never reached the estimator.

    It is still a valid extraction case, so it stays in the file; it simply has
    no quote to score. Dropping it from the cost layer is right, raising is not.
    """
    path = tmp_path / "pipeline_runs.jsonl"
    path.write_text(
        json.dumps({"file": "stalled.pdf", "requirements": {}, "brief_words": [],
                    "estimation": None}) + "\n",
        encoding="utf-8",
    )

    assert sc.load_quote_cases(path) == []


def test_load_quote_cases_is_empty_when_nothing_seeded(tmp_path):
    assert sc.load_quote_cases(tmp_path / "absent.jsonl") == []


def test_an_unbuildable_estimation_is_returned_as_an_error_not_raised(tmp_path):
    """mapping raises EmptyEstimate when an estimation prices nothing at all.

    Both conditions are needed: with no roles but a non-zero total, mapping
    synthesises a single line item from the total instead of raising. It is the
    zero-total-and-no-roles case that has nothing to quote.

    Letting that escape `load_quote_cases` crashes the whole eval command over
    one bad case. It comes back as a case carrying an error instead, so the cost
    layer can score it as the failure it is.
    """
    path = tmp_path / "pipeline_runs.jsonl"
    path.write_text(
        json.dumps({"file": "empty-estimate.pdf", "requirements": {}, "brief_words": [],
                    "estimation": _estimation(role_breakdown=[], total_cost_usd=0)}) + "\n",
        encoding="utf-8",
    )

    cases = sc.load_quote_cases(path)

    assert len(cases) == 1
    assert cases[0]["context"] is None
    assert "EmptyEstimate" in cases[0]["error"]


def test_a_run_that_captures_nothing_leaves_the_existing_cases_alone(tmp_path,
                                                                    monkeypatch):
    """A failed seed must not destroy the case file it could not replace.

    Observed live: the API went down mid-run, all eight briefs failed, and the
    generator wrote an empty file over the previous cases — turning a transient
    outage into lost data and both layers into a SKIP.
    """
    out = tmp_path / "pipeline_runs.jsonl"
    existing = json.dumps({"file": "kept.pdf", "requirements": {},
                           "brief_words": ["android"], "estimation": None}) + "\n"
    out.write_text(existing, encoding="utf-8")

    brief = tmp_path / "brief.pdf"
    brief.write_bytes(_pdf("Android catalog"))
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setattr(sc, "_one_run", lambda path: (_ for _ in ()).throw(
        sc.requests.ConnectionError("API down")))

    rc = sc.main(["--briefs", str(brief), "--out", str(out)])

    assert rc == 1                                   # reported as a failure
    assert out.read_text(encoding="utf-8") == existing   # and changed nothing
