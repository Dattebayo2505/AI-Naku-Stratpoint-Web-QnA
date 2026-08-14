"""extraction_eval — is every extracted requirement traceable to the brief?

The layer measures grounding, not recall: it catches a requirement the model
invented, and by construction cannot catch one it dropped. That asymmetry is
deliberate (an invented requirement gets priced and printed on a commercial
document; a dropped one is a gap the visitor can see), and it is why the metric
is named `brief-grounding` rather than `extraction-accuracy`.
"""

from __future__ import annotations

import pytest

from stratpoint_rag.evaluation import extraction_eval as ee


def _case(**overrides) -> dict:
    """One seeded pipeline run: the extraction plus the brief's word set."""
    case = {
        "file": "rfp-fixture.pdf",
        "requirements": {
            "features": ["user accounts", "product catalog"],
            "constraints": ["PCI compliant payments"],
            "tech_stack": [],
            "target_platform": ["Android"],
        },
        "brief_words": sorted(
            {"user", "accounts", "product", "catalog", "pci", "compliant",
             "payments", "android", "launch", "four", "months"}
        ),
    }
    case.update(overrides)
    return case


def test_a_value_whose_words_are_all_in_the_brief_is_grounded():
    assert ee.is_grounded("product catalog", {"product", "catalog", "user"})


def test_a_value_with_no_support_in_the_brief_is_not_grounded():
    assert not ee.is_grounded("SSO via Okta", {"product", "catalog", "user"})


def test_partial_overlap_counts_as_grounded():
    """A good extractor normalises: '4 months' -> '4-month timeline'.

    Exact-match scoring would report correct behaviour as a hallucination, the
    same miscount the guardrail dataset was fixed for. The bar is that the value
    is *anchored* in the brief, not that it is a substring of it.
    """
    assert ee.is_grounded("4-month launch timeline", {"launch", "four", "months"})


def test_an_empty_value_is_not_counted_either_way():
    """Neither grounded nor ungrounded — it is not a claim about the brief."""
    res = ee.score_case(_case(requirements={"features": ["", "  "], "constraints": [],
                                            "tech_stack": [], "target_platform": []}))
    assert res["total"] == 0


def test_score_case_counts_every_requirement_list():
    res = ee.score_case(_case())

    assert res["total"] == 4          # 2 features + 1 constraint + 1 platform
    assert res["grounded"] == 4
    assert res["ungrounded"] == []


def test_an_invented_feature_is_reported_by_name():
    case = _case()
    case["requirements"]["features"] = ["user accounts", "SSO via Okta"]

    res = ee.score_case(case)

    assert res["grounded"] == 3
    assert res["ungrounded"] == ["SSO via Okta"]


def test_run_aggregates_across_briefs():
    clean = _case()
    dirty = _case(file="rfp-dirty.pdf")
    dirty["requirements"]["features"] = ["blockchain ledger", "product catalog"]

    res = ee.run_extraction_eval([clean, dirty])

    assert res["total"] == 8
    assert res["passed"] == 7
    assert res["pass_rate"] == pytest.approx(7 / 8)
    assert res["failures"] == [{"file": "rfp-dirty.pdf", "value": "blockchain ledger"}]


def test_the_layer_skips_when_no_cases_have_been_seeded():
    res = ee.run_extraction_eval([])

    assert res["total"] == 0
    assert res["pass_rate"] == 0.0
