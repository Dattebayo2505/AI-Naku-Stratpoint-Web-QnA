import pytest

from stratpoint_crawler.config import Settings
from stratpoint_crawler.crawler import PlaywrightFetcher, crawl
from stratpoint_crawler.models import PageRef


@pytest.mark.integration
async def test_fetch_real_homepage(tmp_path):
    s = Settings(delay_min=0.0, delay_max=0.0)
    async with PlaywrightFetcher(s) as fetcher:
        summary = await crawl(
            [PageRef(url="https://stratpoint.com/")],
            settings=s, fetcher=fetcher, out_dir=tmp_path,
            crawled_at="2026-06-14T00:00:00Z",
        )
    assert summary.results[0].status == "ok"
    assert (tmp_path / "pages" / "index.md").stat().st_size > 200
