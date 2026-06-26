from pathlib import Path

import pytest

from stratpoint_crawler.config import Settings
from stratpoint_crawler.sitemap import (
    parse_sitemap_index,
    parse_urlset,
    filter_refs,
    EmptySitemapError,
)

FIX = Path(__file__).parent / "fixtures"


def test_parse_sitemap_index_returns_child_locs():
    xml = (FIX / "sitemap_index.xml").read_bytes()
    children = parse_sitemap_index(xml)
    assert children == [
        "https://stratpoint.com/page-sitemap.xml",
        "https://stratpoint.com/post-sitemap.xml",
    ]


def test_parse_urlset_returns_refs_with_lastmod():
    xml = (FIX / "page-sitemap.xml").read_bytes()
    refs = parse_urlset(xml)
    urls = [r.url for r in refs]
    assert "https://stratpoint.com/" in urls
    assert refs[0].lastmod == "2025-06-01"


def test_filter_refs_dedupes_and_drops_offhost_and_wpcontent():
    xml = (FIX / "page-sitemap.xml").read_bytes()
    refs = filter_refs(parse_urlset(xml), Settings())
    urls = [r.url for r in refs]
    assert urls.count("https://stratpoint.com/about/") == 1
    assert "https://other.com/spam/" not in urls
    assert all("/wp-content/" not in u for u in urls)


def test_empty_index_raises():
    empty = b'<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></sitemapindex>'
    with pytest.raises(EmptySitemapError):
        parse_sitemap_index(empty, require_nonempty=True)
