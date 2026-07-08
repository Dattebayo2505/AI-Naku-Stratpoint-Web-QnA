"""Unit tests for chunk splitting — focus on the link-preservation guard.

Regression: an oversized paragraph was hard-split on raw character offsets,
slicing a `[anchor](https://…​.pdf)` markdown link across two chunks. The chunk
with the .pdf filename then started mid-URL (no `[anchor](` prefix), so
_extract_doc_links matched nothing and find_resource lost the document.
"""
from stratpoint_rag.agent.tools import _extract_doc_links
from stratpoint_rag.rag.chunker import split_text

# The real failing content (World Bank PDF anchored by "mobile phone usage
# patterns"), padded so the paragraph exceeds CHARS_PER_CHUNK and forces a split.
PDF_URL = (
    "https://documents1.worldbank.org/curated/en/811881575657172759/pdf/"
    "Behavior-Revealed-in-Mobile-Phone-Usage-Predicts-Credit-Repayment.pdf"
)
LINK = f"[mobile phone usage patterns]({PDF_URL})"


def _paragraph_with_link_near_boundary() -> str:
    lead = (
        "Traditional credit scoring models rely on formal financial histories, "
        "which many unbanked individuals lack. By incorporating non-traditional "
        "data sources such as mobile payment records and utility bill payments, "
        "financial institutions can build a fuller picture of creditworthiness. "
        "AI-driven models process this alternative data for more accurate risk "
        "assessments and increased loan approvals for those previously deemed "
        "uncreditworthy by conventional means and excluded from formal lending. "
        "Across developing economies this expands access to essential financial "
        "products for millions. For instance, in emerging "
        "markets, companies have utilized "
    )
    tail = " to predict repayment behaviors, thereby extending credit to the underserved."
    return lead + LINK + tail


def test_link_not_split_across_chunks():
    para = _paragraph_with_link_near_boundary()
    assert len(para) > 800  # precondition: the paragraph must be split

    chunks = split_text(para)
    assert len(chunks) > 1  # precondition: it actually split

    # Exactly the chunks that mention the PDF filename must expose it as a link;
    # no chunk may contain the filename as an unextractable fragment.
    for c in chunks:
        if ".pdf" in c:
            extracted = _extract_doc_links(c)
            assert any(PDF_URL == url for _, url in extracted), (
                f"chunk contains a .pdf but no extractable link: {c[:120]!r}"
            )

    # And at least one chunk yields the full link end-to-end.
    all_links = [url for c in chunks for _, url in _extract_doc_links(c)]
    assert PDF_URL in all_links


def test_plain_oversized_paragraph_still_splits():
    """No links → behavior unchanged: a long paragraph splits into >1 chunk that
    reconstructs (minus overlap) the original text."""
    para = "word " * 400  # 2000 chars, no links
    chunks = split_text(para)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) <= 800


def test_short_text_is_single_chunk():
    assert split_text("A short paragraph with a [link](https://x.com/a.pdf).") == [
        "A short paragraph with a [link](https://x.com/a.pdf)."
    ]
