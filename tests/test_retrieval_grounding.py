"""Regression tests for chunk-granularity retrieval misses.

Guards the bug where a high-value sentence, diluted inside a ~1600-char chunk,
ranked below dozens of generic chunks so `find_resource`/`search_stratpoint`
reported the fact as absent even though it was in the corpus. Shrinking the
chunk size (chunker.CHARS_PER_CHUNK) restored ranking.

Integration-marked: requires the built Chroma store (`stratpoint-rag-ingest`)
and the local embedding model, so it is deselected from the default unit run.
"""
import pytest

from stratpoint_rag.rag.retrieve import retrieve

# (query, expected source slug, a needle string, a downloadable-doc URL fragment)
# — both cases are near-verbatim quotes a visitor might paste from a blog page.
CASES = [
    pytest.param(
        "A Nucleus Research study shows Cloud adoption",
        "2023__08__29__boost-supply-chain-resilience-and-efficiency-through-cloud-data-and-ai",
        "Nucleus Research",
        "the-value-of-improved-availability-security-and-performance.pdf",
        id="nucleus-cloud-adoption",
    ),
    pytest.param(
        "A robust and flexible infrastructure ensures that your organization can quickly "
        "respond to market changes and scale operations as needed. Can your infrastructure "
        "handle increasing data volumes and support future growth? The World Economic Forum’s?",
        "2024__09__13__evaluating-your-company-digital-maturity",
        "robust and flexible infrastructure",
        "WEF_The_Global_Risks_Report_2024.pdf",
        id="wef-robust-infrastructure",
    ),
    pytest.param(
        # Short query matching a PDF link *anchor* buried in an off-topic
        # (financial-inclusion) chunk. Failed two ways before B+C: the true-NN
        # chunk was missed by HNSW at k=10, and the link was split across a chunk
        # boundary so its .pdf was unextractable.
        "mobile phone usage patterns",
        "2025__03__14__enhancing-financial-inclusion-how-data-analytics-bridges-the-gap",
        "mobile phone usage patterns",
        "Behavior-Revealed-in-Mobile-Phone-Usage-Predicts-Credit-Repayment.pdf",
        id="mobile-phone-usage-anchor",
    ),
]


@pytest.mark.integration
@pytest.mark.parametrize("query, slug, needle, pdf_fragment", CASES)
def test_near_verbatim_fact_is_retrieved(query, slug, needle, pdf_fragment):
    hits = retrieve(query, k=10)
    slugs = [h.slug for h in hits]
    assert slug in slugs, f"target page {slug} missing from top-10: {slugs}"
    # The specific chunk carrying the fact (and its download link) must surface —
    # retrieving the page but not the fact-bearing chunk was the original failure.
    assert any(needle in h.text for h in hits), f"needle {needle!r} not in retrieved chunks"
    assert any(pdf_fragment in h.text for h in hits), f"doc link {pdf_fragment!r} not retrieved"


@pytest.mark.integration
def test_find_resource_surfaces_anchor_matched_pdf():
    """End-to-end seam: the user-facing tool must return the World Bank PDF for a
    query that matches only the link's anchor text (the reported failure)."""
    from stratpoint_rag.agent.tools import find_resource

    out = find_resource.invoke("mobile phone usage patterns")
    assert "Behavior-Revealed-in-Mobile-Phone-Usage-Predicts-Credit-Repayment.pdf" in out
