from stratpoint_crawl.models import PageRef, PageContent, PageResult


def test_pageref_lastmod_optional():
    assert PageRef(url="https://x/").lastmod is None
    assert PageRef(url="https://x/", lastmod="2025-06-01").lastmod == "2025-06-01"


def test_pagecontent_defaults():
    c = PageContent(url="https://x/", title="T", markdown="# T",
                    text_len=3, content_hash="sha256:abc")
    assert c.thin is False


def test_pageresult_carries_content():
    c = PageContent(url="https://x/", title="T", markdown="b",
                    text_len=1, content_hash="sha256:abc")
    r = PageResult(url="https://x/", slug="index", status="ok", content=c)
    assert r.error is None and r.content.title == "T"


def test_pageresult_carries_lastmod():
    r = PageResult(url="https://x/", slug="x", status="ok", lastmod="2025-06-01")
    assert r.lastmod == "2025-06-01"
    assert PageResult(url="https://x/", slug="x", status="failed").lastmod is None


def test_crawl_summary_holds_results_skipped_removed():
    from stratpoint_crawl.models import CrawlSummary
    s = CrawlSummary(results=[], skipped=3, removed=["https://x/gone/"])
    assert s.skipped == 3
    assert s.removed == ["https://x/gone/"]
    assert s.results == []
