from stratpoint_crawl.config import Settings


def test_defaults_match_spec():
    s = Settings()
    assert s.host == "stratpoint.com"
    assert s.sitemap_index_url == "https://stratpoint.com/sitemap_index.xml"
    assert s.concurrency == 4
    assert s.delay_min == 0.5
    assert s.delay_max == 1.5
    assert s.nav_timeout_ms == 30_000
    assert s.thin_content_min == 200
    assert "nav" in s.chrome_selectors and "footer" in s.chrome_selectors


def test_overrides_apply():
    s = Settings(concurrency=2, delay_min=0.1, delay_max=0.2)
    assert s.concurrency == 2
    assert (s.delay_min, s.delay_max) == (0.1, 0.2)
