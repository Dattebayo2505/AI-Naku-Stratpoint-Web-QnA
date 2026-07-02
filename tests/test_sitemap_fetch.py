import httpx
import pytest
import respx

from stratpoint_crawl.config import Settings
from stratpoint_crawl.sitemap import discover_page_refs, EmptySitemapError

INDEX = b"""<?xml version="1.0"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://stratpoint.com/page-sitemap.xml</loc></sitemap>
</sitemapindex>"""

URLSET = b"""<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://stratpoint.com/about/</loc><lastmod>2025-05-20</lastmod></url>
</urlset>"""


@respx.mock
async def test_discover_page_refs_walks_index_then_children():
    s = Settings()
    respx.get(s.sitemap_index_url).mock(return_value=httpx.Response(200, content=INDEX))
    respx.get("https://stratpoint.com/page-sitemap.xml").mock(
        return_value=httpx.Response(200, content=URLSET))

    refs = await discover_page_refs(s)
    assert [r.url for r in refs] == ["https://stratpoint.com/about/"]


@respx.mock
async def test_discover_raises_when_index_unreachable():
    s = Settings()
    respx.get(s.sitemap_index_url).mock(return_value=httpx.Response(503))
    with pytest.raises(EmptySitemapError):
        await discover_page_refs(s)
