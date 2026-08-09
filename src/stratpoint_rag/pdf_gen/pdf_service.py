"""Headless-Chromium HTML -> PDF, sync and async.

Playwright is already a dependency (the crawler owns it) and already installed
by the same ``playwright install chromium`` step, so this adds a rendering
engine rather than a second one. WeasyPrint would have needed a whole native
toolchain on the LXC and does not support ``clip-path``, which the template's
chevron ribbon is built from.

Four decisions worth reading before editing:

- **The network is blocked by default.** Every http(s) request is aborted at the
  route layer. Not for tidiness: a `<link>` to a font CDN from a container with
  no egress does not error, it *stalls* until the navigation timeout, so a
  render that should take 1.5s takes 30s and then produces a PDF with the wrong
  fonts anyway. Assets are inlined as data: URIs instead (``pdf_gen/assets.py``).
  ``allow_network=True`` exists for local debugging and is never used in
  production.

- **``prefer_css_page_size`` with zero margins.** The template owns its geometry
  via ``@page { size: A4 portrait; margin: 12mm 14mm }``. Passing margins here
  as well applies them *on top of* the CSS ones, which silently reflows the
  two-page layout onto three. Zero here means "the stylesheet decides".

- **``emulate_media("print")`` before ``page.pdf()``.** ``page.pdf()`` already
  implies print media in Chromium, but the template's ``@media print`` block is
  what strips the on-screen drop shadows and page gutters; emulating explicitly
  means a screenshot taken for debugging shows what the PDF will show.

- **One browser per render, bounded by a semaphore.** The sync Playwright API
  refuses to be driven from a thread other than the one that created it, and
  FastAPI runs sync endpoints in a threadpool, so a shared long-lived browser is
  not available. Launch cost is ~300-600ms against a ~1s render; the semaphore
  is a memory guard for the 6GB LXC, where unbounded concurrent Chromiums are
  the realistic way to OOM the box.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import Error as AsyncPlaywrightError
from playwright.async_api import async_playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from stratpoint_rag.pdf_gen import config

log = logging.getLogger(__name__)

__all__ = [
    "PdfOptions",
    "PdfRenderError",
    "agenerate_pdf_from_html",
    "generate_pdf_from_html",
]


class PdfRenderError(RuntimeError):
    """The browser could not produce a PDF. Carries the underlying cause."""


@dataclass(frozen=True)
class PdfOptions:
    """Print parameters. Defaults are the ones the quote template expects."""

    page_format: str = "A4"
    print_background: bool = True
    # Zero, and prefer_css_page_size — the template's @page rule owns the
    # margins. Setting both double-applies them and reflows the layout.
    margin: dict[str, str] = field(
        default_factory=lambda: {"top": "0", "right": "0", "bottom": "0", "left": "0"}
    )
    prefer_css_page_size: bool = True
    scale: float = 1.0
    landscape: bool = False
    timeout_ms: int | None = None
    allow_network: bool = False
    # When set, the HTML is loaded from a temp file inside this directory so
    # relative asset paths resolve. Inlining via data: URIs is the normal route.
    base_dir: Path | None = None

    def resolved_timeout(self) -> int:
        return self.timeout_ms if self.timeout_ms is not None else config.pdf_timeout_ms()

    def pdf_kwargs(self) -> dict:
        return {
            "format": self.page_format,
            "print_background": self.print_background,
            "margin": dict(self.margin),
            "prefer_css_page_size": self.prefer_css_page_size,
            "scale": self.scale,
            "landscape": self.landscape,
        }


# Bounds resident Chromiums, not throughput. See config.pdf_max_concurrency.
_slots = threading.BoundedSemaphore(config.pdf_max_concurrency())
_slots_size = config.pdf_max_concurrency()


def _semaphore() -> threading.BoundedSemaphore:
    """Rebuild the semaphore if the configured size changed (tests monkeypatch it)."""
    global _slots, _slots_size
    size = config.pdf_max_concurrency()
    if size != _slots_size:
        _slots, _slots_size = threading.BoundedSemaphore(size), size
    return _slots


def _launch_kwargs() -> dict:
    kwargs: dict = {"args": config.browser_args()}
    executable = config.chromium_executable()
    if executable:
        kwargs["executable_path"] = executable
    return kwargs


# Launch is the flaky step, not the render: a cold container can hit a transient
# "Target closed"/"browserType.launch: Timeout" while the filesystem or an
# earlier Chromium is still settling. The render itself either works or is a
# template bug, and retrying it just pays the cost twice.
_launch_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
    retry=retry_if_exception_type((PlaywrightError, AsyncPlaywrightError)),
    reraise=True,
)


@_launch_retry
def _launch(playwright):
    return playwright.chromium.launch(**_launch_kwargs())


@_launch_retry
async def _alaunch(playwright):
    return await playwright.chromium.launch(**_launch_kwargs())


def _block_external(route, request) -> None:
    """Abort anything that would leave the machine.

    ``abort`` rather than ``fulfill`` with an empty body: an aborted image falls
    back to alt text, whereas an empty 200 renders as a broken-image box in the
    middle of a client's quote.
    """
    if request.url.startswith(("http://", "https://")):
        log.debug("proposal render blocked external request: %s", request.url)
        route.abort()
    else:
        route.continue_()


async def _ablock_external(route, request) -> None:
    if request.url.startswith(("http://", "https://")):
        log.debug("proposal render blocked external request: %s", request.url)
        await route.abort()
    else:
        await route.continue_()


def _temp_html(html: str, base_dir: Path) -> Path:
    """Write the document inside ``base_dir`` so relative URLs resolve to it."""
    import uuid

    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / f".render-{uuid.uuid4().hex}.html"
    path.write_text(html, encoding="utf-8")
    return path


def generate_pdf_from_html(
    html: str,
    output_path: str | Path,
    options: PdfOptions | None = None,
) -> Path:
    """Render ``html`` to a PDF at ``output_path``. Returns the path written.

    Safe to call from a FastAPI ``def`` endpoint or an agent tool: both run on a
    worker thread with no running event loop, which is what the sync Playwright
    API requires. From inside async code, use ``agenerate_pdf_from_html``.
    """
    opts = options or PdfOptions()
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    temp: Path | None = None
    with _semaphore():
        try:
            with sync_playwright() as p:
                browser = _launch(p)
                try:
                    page = browser.new_page()
                    page.set_default_timeout(opts.resolved_timeout())
                    page.set_default_navigation_timeout(opts.resolved_timeout())
                    if not opts.allow_network:
                        page.route("**/*", _block_external)

                    if opts.base_dir is not None:
                        temp = _temp_html(html, opts.base_dir)
                        page.goto(temp.as_uri(), wait_until="load")
                    else:
                        page.set_content(html, wait_until="load")

                    page.emulate_media(media="print")
                    data = page.pdf(**opts.pdf_kwargs())
                finally:
                    browser.close()
        except (PlaywrightError, OSError) as ex:
            raise PdfRenderError(f"PDF render failed: {type(ex).__name__}: {ex}") from ex
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)

    out.write_bytes(data)
    return out


async def agenerate_pdf_from_html(
    html: str,
    output_path: str | Path,
    options: PdfOptions | None = None,
) -> Path:
    """Async twin of :func:`generate_pdf_from_html`, for callers already in a loop.

    Not a wrapper around the sync function: calling sync Playwright from inside
    a running event loop raises outright, so the two implementations have to be
    separate. The semaphore is *not* taken here — an async caller is on the
    event-loop thread and blocking it on a threading primitive would stall every
    other request in the process. Bound async concurrency at the caller.
    """
    opts = options or PdfOptions()
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    temp: Path | None = None
    try:
        async with async_playwright() as p:
            browser = await _alaunch(p)
            try:
                page = await browser.new_page()
                page.set_default_timeout(opts.resolved_timeout())
                page.set_default_navigation_timeout(opts.resolved_timeout())
                if not opts.allow_network:
                    await page.route("**/*", _ablock_external)

                if opts.base_dir is not None:
                    temp = _temp_html(html, opts.base_dir)
                    await page.goto(temp.as_uri(), wait_until="load")
                else:
                    await page.set_content(html, wait_until="load")

                await page.emulate_media(media="print")
                data = await page.pdf(**opts.pdf_kwargs())
            finally:
                await browser.close()
    except (AsyncPlaywrightError, OSError) as ex:
        raise PdfRenderError(f"PDF render failed: {type(ex).__name__}: {ex}") from ex
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)

    out.write_bytes(data)
    return out
