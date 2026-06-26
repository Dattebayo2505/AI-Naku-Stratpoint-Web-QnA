from stratpoint_crawler.config import Settings
from stratpoint_crawler.models import PageRef
from stratpoint_crawler.crawler import crawl


class FakeFetcher:
    """Returns canned HTML; raises for any URL containing 'dead'."""

    def __init__(self):
        self.calls = []

    async def fetch(self, url: str) -> str:
        self.calls.append(url)
        if "dead" in url:
            raise RuntimeError("boom")
        body = "<p>" + ("word " * 80) + "</p>"
        return f"<html><body><main><h1>Page</h1>{body}</main></body></html>"


import json


async def test_crawl_returns_ok_and_failed_results(tmp_path):
    refs = [
        PageRef(url="https://stratpoint.com/about/"),
        PageRef(url="https://stratpoint.com/dead/"),
    ]
    s = Settings(concurrency=2, delay_min=0.0, delay_max=0.0, max_attempts=2)
    summary = await crawl(refs, settings=s, fetcher=FakeFetcher(),
                          out_dir=tmp_path, crawled_at="2026-06-14T00:00:00Z")
    by_url = {r.url: r for r in summary.results}
    assert by_url["https://stratpoint.com/about/"].status == "ok"
    assert by_url["https://stratpoint.com/dead/"].status == "failed"
    assert (tmp_path / "pages" / "about.md").exists()
    assert (tmp_path / "index.jsonl").exists()
    assert summary.skipped == 0 and summary.removed == []


async def test_crawl_respects_limit(tmp_path):
    refs = [PageRef(url=f"https://stratpoint.com/p{i}/") for i in range(5)]
    s = Settings(concurrency=2, delay_min=0.0, delay_max=0.0)
    summary = await crawl(refs, settings=s, fetcher=FakeFetcher(),
                          out_dir=tmp_path, crawled_at="t", limit=2)
    assert len(summary.results) == 2


def _seed_index(tmp_path, records):
    (tmp_path / "index.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


async def test_incremental_skips_unchanged_and_crawls_changed(tmp_path):
    # Prior manifest: /a unchanged (will skip), /b changed (will recrawl).
    _seed_index(tmp_path, [
        {"url": "https://stratpoint.com/a/", "slug": "a", "lastmod": "2025-01-01",
         "status": "ok", "content_hash": "sha256:a", "title": "A", "text_len": 9,
         "crawled_at": "2026-06-10T00:00:00Z", "error": None},
        {"url": "https://stratpoint.com/b/", "slug": "b", "lastmod": "2025-01-01",
         "status": "ok", "content_hash": "sha256:b", "title": "B", "text_len": 9,
         "crawled_at": "2026-06-10T00:00:00Z", "error": None},
    ])
    refs = [
        PageRef(url="https://stratpoint.com/a/", lastmod="2025-01-01"),   # unchanged -> skip
        PageRef(url="https://stratpoint.com/b/", lastmod="2025-09-01"),   # changed -> recrawl
        PageRef(url="https://stratpoint.com/c/", lastmod="2025-09-01"),   # new -> crawl
    ]
    fetcher = FakeFetcher()
    s = Settings(concurrency=2, delay_min=0.0, delay_max=0.0)
    summary = await crawl(refs, settings=s, fetcher=fetcher, out_dir=tmp_path,
                          crawled_at="2026-06-14T00:00:00Z", incremental=True)

    fetched = set(fetcher.calls)
    assert "https://stratpoint.com/a/" not in fetched          # skipped: never fetched
    assert "https://stratpoint.com/b/" in fetched              # changed: fetched
    assert "https://stratpoint.com/c/" in fetched              # new: fetched
    assert summary.skipped == 1
    assert summary.removed == []

    rows = {json.loads(l)["url"]: json.loads(l)
            for l in (tmp_path / "index.jsonl").read_text(encoding="utf-8").splitlines()}
    assert set(rows) == {f"https://stratpoint.com/{p}/" for p in "abc"}   # full corpus
    assert rows["https://stratpoint.com/a/"]["status"] == "skipped"
    assert rows["https://stratpoint.com/a/"]["content_hash"] == "sha256:a"  # carried


async def test_incremental_reports_removed_pages(tmp_path):
    _seed_index(tmp_path, [
        {"url": "https://stratpoint.com/gone/", "slug": "gone", "lastmod": "2025-01-01",
         "status": "ok", "content_hash": "sha256:g", "title": "G", "text_len": 9,
         "crawled_at": "2026-06-10T00:00:00Z", "error": None},
    ])
    refs = [PageRef(url="https://stratpoint.com/a/", lastmod="2025-01-01")]   # /gone not in sitemap
    s = Settings(concurrency=2, delay_min=0.0, delay_max=0.0)
    summary = await crawl(refs, settings=s, fetcher=FakeFetcher(), out_dir=tmp_path,
                          crawled_at="2026-06-14T00:00:00Z", incremental=True)
    assert summary.removed == ["https://stratpoint.com/gone/"]
    assert summary.skipped == 0   # removed pages are not counted as skipped

    # removed page is still carried forward into the manifest (non-destructive)
    rows = {json.loads(l)["url"]: json.loads(l)
            for l in (tmp_path / "index.jsonl").read_text(encoding="utf-8").splitlines()}
    assert rows["https://stratpoint.com/gone/"]["status"] == "skipped"
    assert rows["https://stratpoint.com/a/"]["status"] == "ok"


async def test_force_recrawls_despite_unchanged_lastmod(tmp_path):
    _seed_index(tmp_path, [
        {"url": "https://stratpoint.com/a/", "slug": "a", "lastmod": "2025-01-01",
         "status": "ok", "content_hash": "sha256:a", "title": "A", "text_len": 9,
         "crawled_at": "2026-06-10T00:00:00Z", "error": None},
    ])
    refs = [PageRef(url="https://stratpoint.com/a/", lastmod="2025-01-01")]
    fetcher = FakeFetcher()
    s = Settings(concurrency=2, delay_min=0.0, delay_max=0.0)
    summary = await crawl(refs, settings=s, fetcher=fetcher, out_dir=tmp_path,
                          crawled_at="2026-06-14T00:00:00Z", incremental=True, force=True)
    assert "https://stratpoint.com/a/" in fetcher.calls   # forced refetch
    assert summary.skipped == 0
