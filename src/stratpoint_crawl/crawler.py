import asyncio
import random
from pathlib import Path
from typing import Protocol

from tenacity import retry, stop_after_attempt, wait_exponential

from .config import Settings
from .extract import extract
from .models import CrawlSummary, PageRef, PageResult
from .state import load_previous, should_recrawl
from .storage import Writer, slugify


class Fetcher(Protocol):
    async def fetch(self, url: str) -> str: ...


async def _fetch_one(fetcher: Fetcher, ref: PageRef, settings: Settings,
                     writer: Writer, sem: asyncio.Semaphore) -> PageResult:
    slug = slugify(ref.url)

    @retry(stop=stop_after_attempt(settings.max_attempts),
           wait=wait_exponential(multiplier=0.2, max=5),
           reraise=True)
    async def _attempt() -> str:
        return await fetcher.fetch(ref.url)

    async with sem:
        if settings.delay_max > 0:
            await asyncio.sleep(random.uniform(settings.delay_min, settings.delay_max))
        try:
            html = await _attempt()
        except Exception as exc:  # noqa: BLE001 - record failure and continue the crawl
            return PageResult(url=ref.url, slug=slug, status="failed",
                              error=str(exc), lastmod=ref.lastmod)

    content = extract(html, url=ref.url, settings=settings)
    writer.write_page(slug, content, raw_html=html, lastmod=ref.lastmod)
    return PageResult(url=ref.url, slug=slug, status="ok",
                      content=content, lastmod=ref.lastmod)


async def crawl(refs: list[PageRef], *, settings: Settings, fetcher: Fetcher,
                out_dir: Path, crawled_at: str, limit: int | None = None,
                incremental: bool = False, force: bool = False) -> CrawlSummary:
    writer = Writer(out_dir=out_dir, crawled_at=crawled_at, save_html=settings.save_html)
    previous = load_previous(Path(out_dir) / "index.jsonl") if incremental else {}

    if incremental:
        selected = [r for r in refs if should_recrawl(r, previous.get(r.url), force)]
    else:
        selected = list(refs)
    if limit is not None:
        selected = selected[:limit]

    sem = asyncio.Semaphore(settings.concurrency)
    tasks = [_fetch_one(fetcher, ref, settings, writer, sem) for ref in selected]
    results = await asyncio.gather(*tasks)

    selected_urls = {r.url for r in selected}
    sitemap_urls = {r.url for r in refs}
    carried = [previous[u] for u in previous if u not in selected_urls]   # every un-recrawled page
    removed = [u for u in previous if u not in sitemap_urls]
    skipped = sum(1 for u in previous if u not in selected_urls and u in sitemap_urls)

    writer.write_index(results, carried_records=carried)
    return CrawlSummary(results=results, skipped=skipped, removed=removed)


class PlaywrightFetcher:
    """Renders pages with a shared headless Chromium; one BrowserContext per call."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._browser = None
        self._pw = None

    async def __aenter__(self) -> "PlaywrightFetcher":
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def fetch(self, url: str) -> str:
        context = await self._browser.new_context()
        try:
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded",
                            timeout=self.settings.nav_timeout_ms)
            for sel in self.settings.consent_button_selectors:
                try:
                    btn = page.locator(sel).first
                    if await btn.count() > 0:
                        await btn.click(timeout=2000)
                        break
                except Exception:  # noqa: BLE001 - consent dismissal is best-effort
                    pass
            return await page.content()
        finally:
            await context.close()
