import httpx
from lxml import etree

from .config import Settings
from .models import PageRef

_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


class EmptySitemapError(RuntimeError):
    """Raised when a sitemap yields no usable URLs."""


def parse_sitemap_index(xml: bytes, *, require_nonempty: bool = False) -> list[str]:
    root = etree.fromstring(xml)
    locs = [el.text.strip() for el in root.findall(".//sm:sitemap/sm:loc", _NS) if el.text]
    if require_nonempty and not locs:
        raise EmptySitemapError("sitemap index contained no child sitemaps")
    return locs


def parse_urlset(xml: bytes) -> list[PageRef]:
    root = etree.fromstring(xml)
    refs: list[PageRef] = []
    for url_el in root.findall(".//sm:url", _NS):
        loc_el = url_el.find("sm:loc", _NS)
        if loc_el is None or not loc_el.text:
            continue
        mod_el = url_el.find("sm:lastmod", _NS)
        lastmod = mod_el.text.strip() if mod_el is not None and mod_el.text else None
        refs.append(PageRef(url=loc_el.text.strip(), lastmod=lastmod))
    return refs


def filter_refs(refs: list[PageRef], settings: Settings) -> list[PageRef]:
    seen: set[str] = set()
    out: list[PageRef] = []
    for ref in refs:
        if settings.host not in ref.url:
            continue
        if "/wp-content/" in ref.url:
            continue
        if ref.url in seen:
            continue
        seen.add(ref.url)
        out.append(ref)
    return out


async def _get_bytes(client: httpx.AsyncClient, url: str) -> bytes | None:
    try:
        resp = await client.get(url, follow_redirects=True, timeout=30.0)
        resp.raise_for_status()
        return resp.content
    except httpx.HTTPError:
        return None


async def discover_page_refs(settings: Settings) -> list[PageRef]:
    async with httpx.AsyncClient() as client:
        index_xml = await _get_bytes(client, settings.sitemap_index_url)
        if index_xml is None:
            raise EmptySitemapError(
                f"could not fetch sitemap index at {settings.sitemap_index_url}; "
                "a --seed-url link-crawl mode is not implemented yet"
            )
        child_urls = parse_sitemap_index(index_xml, require_nonempty=True)

        all_refs: list[PageRef] = []
        for child in child_urls:
            xml = await _get_bytes(client, child)
            if xml is None:
                continue
            all_refs.extend(parse_urlset(xml))

    refs = filter_refs(all_refs, settings)
    if not refs:
        raise EmptySitemapError("sitemap discovery yielded zero content URLs")
    return refs
