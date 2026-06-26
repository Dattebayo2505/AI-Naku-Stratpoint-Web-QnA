# Stratpoint.com Web Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a `uv`-managed, sitemap-driven Playwright crawler that turns stratpoint.com into per-page Markdown plus an `index.jsonl` manifest for a downstream RAG pipeline.

**Architecture:** Discover URLs from the WordPress `sitemap_index.xml` over `httpx`; render each page with one headless Chromium (4 concurrent `BrowserContext` workers, jittered politeness delay); strip site chrome with `selectolax` and convert the body to Markdown with `markdownify`; write one `.md` per page (YAML frontmatter) and an atomic `index.jsonl`. An incremental-mode seam (`state.py`, `--incremental`, `content_hash`) is wired but no-op for now.

**Tech Stack:** Python 3.13, uv, Playwright (Chromium), httpx, lxml, selectolax, markdownify, tenacity, pydantic; pytest + pytest-asyncio + respx for tests.

**Spec:** `docs/superpowers/specs/2026-06-13-stratpoint-crawler-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | uv project metadata, deps, `stratpoint-crawler` script, pytest config |
| `.python-version` | Pins CPython 3.13 |
| `src/stratpoint_crawler/__init__.py` | Package marker + version |
| `src/stratpoint_crawler/config.py` | `pydantic` settings: concurrency, delay bounds, timeout, paths, chrome selectors, host |
| `src/stratpoint_crawler/models.py` | `PageRef`, `PageContent`, `PageResult` pydantic models (shared types) |
| `src/stratpoint_crawler/sitemap.py` | Fetch + parse nested sitemaps → `list[PageRef]` |
| `src/stratpoint_crawler/extract.py` | Rendered HTML → cleaned Markdown `PageContent` |
| `src/stratpoint_crawler/storage.py` | Slugify, write `.md` + frontmatter, atomic `index.jsonl`, optional raw HTML, run report |
| `src/stratpoint_crawler/state.py` | Incremental seam: `load_previous`, `should_recrawl` (no-op today) |
| `src/stratpoint_crawler/crawler.py` | Playwright orchestration: async worker pool, retries, run summary |
| `src/stratpoint_crawler/cli.py` | argparse entrypoint, wires everything, stamps `crawled_at` |
| `src/stratpoint_crawler/__main__.py` | `python -m stratpoint_crawler` → `cli.main()` |
| `tests/test_sitemap.py` | Sitemap parsing, host filter, dedup, empty-sitemap error |
| `tests/test_extract.py` | Chrome stripping, title, link preservation, thin-content, hash |
| `tests/test_storage.py` | Slug, frontmatter, atomic jsonl, `--save-html` toggle |
| `tests/test_state.py` | `should_recrawl` True today; `load_previous` parsing |
| `tests/fixtures/` | Canned XML + HTML for offline tests |

**Type contracts** (defined in Task 3, used everywhere after):

```python
# models.py
class PageRef(BaseModel):
    url: str
    lastmod: str | None = None

class PageContent(BaseModel):
    url: str
    title: str
    markdown: str
    text_len: int
    content_hash: str          # "sha256:<hex>"
    thin: bool = False         # text_len < THIN_CONTENT_MIN

class PageResult(BaseModel):
    url: str
    slug: str
    status: str                # "ok" | "failed" | "skipped"
    error: str | None = None
    content: PageContent | None = None
```

---

## Task 1: Project scaffold with uv

**Files:**
- Create: `pyproject.toml`, `.python-version`, `src/stratpoint_crawler/__init__.py`

- [ ] **Step 1: Pin Python and init the package layout**

Run:
```bash
cd "C:/Users/seank/Desktop/ETC/DLSU/AA_STAI100_Stratpoint_RAG"
uv python pin 3.13
uv init --package --name stratpoint-crawler --no-readme
```
Expected: creates `pyproject.toml` and `.python-version`. If `uv init` creates a sample `src/stratpoint_crawler/__init__.py`, keep it; otherwise create it next.

- [ ] **Step 2: Set `__init__.py` content**

Create/overwrite `src/stratpoint_crawler/__init__.py`:
```python
"""Sitemap-driven Playwright crawler for stratpoint.com."""

__version__ = "0.1.0"
```

- [ ] **Step 3: Add runtime and dev dependencies**

Run:
```bash
uv add playwright httpx lxml selectolax markdownify tenacity pydantic
uv add --dev pytest pytest-asyncio respx
uv run playwright install chromium
```
Expected: deps resolve into `pyproject.toml` and a `uv.lock` is written; Chromium downloads.

- [ ] **Step 4: Configure the script entry point and pytest**

Edit `pyproject.toml` so it contains these sections (merge with what `uv init` generated; keep the generated `[project]` name/version/requires-python):
```toml
[project.scripts]
stratpoint-crawler = "stratpoint_crawler.cli:main"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

- [ ] **Step 5: Verify the package imports**

Run:
```bash
uv run python -c "import stratpoint_crawler; print(stratpoint_crawler.__version__)"
```
Expected: prints `0.1.0`.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock .python-version src/stratpoint_crawler/__init__.py
git commit -m "chore: scaffold uv project with crawler dependencies"
```

---

## Task 2: Configuration module

**Files:**
- Create: `src/stratpoint_crawler/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py`:
```python
from stratpoint_crawler.config import Settings


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: stratpoint_crawler.config`.

- [ ] **Step 3: Write minimal implementation**

Create `src/stratpoint_crawler/config.py`:
```python
from pydantic import BaseModel


class Settings(BaseModel):
    host: str = "stratpoint.com"
    sitemap_index_url: str = "https://stratpoint.com/sitemap_index.xml"

    concurrency: int = 4
    delay_min: float = 0.5
    delay_max: float = 1.5
    nav_timeout_ms: int = 30_000
    max_attempts: int = 3

    thin_content_min: int = 200
    title_suffix: str = " - Stratpoint"

    # CSS selectors whose matched elements are removed before extraction.
    chrome_selectors: tuple[str, ...] = (
        "nav", "header", "footer", "script", "style", "noscript",
        "[class*='cookie']", "[id*='cookie']", "[class*='consent']",
        "[class*='share']", "[class*='social']", "form",
    )

    # Best-effort cookie/consent dismiss button selectors (non-fatal if absent).
    consent_button_selectors: tuple[str, ...] = (
        "#onetrust-accept-btn-handler",
        "button[aria-label*='accept' i]",
        "button:has-text('Accept')",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/stratpoint_crawler/config.py tests/test_config.py
git commit -m "feat: add Settings config with spec defaults"
```

---

## Task 3: Shared data models

**Files:**
- Create: `src/stratpoint_crawler/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_models.py`:
```python
from stratpoint_crawler.models import PageRef, PageContent, PageResult


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: stratpoint_crawler.models`.

- [ ] **Step 3: Write minimal implementation**

Create `src/stratpoint_crawler/models.py`:
```python
from pydantic import BaseModel


class PageRef(BaseModel):
    url: str
    lastmod: str | None = None


class PageContent(BaseModel):
    url: str
    title: str
    markdown: str
    text_len: int
    content_hash: str
    thin: bool = False


class PageResult(BaseModel):
    url: str
    slug: str
    status: str
    error: str | None = None
    content: PageContent | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/stratpoint_crawler/models.py tests/test_models.py
git commit -m "feat: add shared PageRef/PageContent/PageResult models"
```

---

## Task 4: Sitemap parsing (pure functions)

**Files:**
- Create: `src/stratpoint_crawler/sitemap.py`
- Test: `tests/test_sitemap.py`, `tests/fixtures/sitemap_index.xml`, `tests/fixtures/page-sitemap.xml`

- [ ] **Step 1: Create XML fixtures**

Create `tests/fixtures/sitemap_index.xml`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://stratpoint.com/page-sitemap.xml</loc><lastmod>2025-06-01T00:00:00+00:00</lastmod></sitemap>
  <sitemap><loc>https://stratpoint.com/post-sitemap.xml</loc><lastmod>2025-05-01T00:00:00+00:00</lastmod></sitemap>
</sitemapindex>
```

Create `tests/fixtures/page-sitemap.xml`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://stratpoint.com/</loc><lastmod>2025-06-01</lastmod></url>
  <url><loc>https://stratpoint.com/about/</loc><lastmod>2025-05-20</lastmod></url>
  <url><loc>https://stratpoint.com/about/</loc><lastmod>2025-05-20</lastmod></url>
  <url><loc>https://other.com/spam/</loc></url>
  <url><loc>https://stratpoint.com/wp-content/uploads/x.jpg</loc></url>
</urlset>
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_sitemap.py`:
```python
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
    assert urls.count("https://stratpoint.com/about/") == 1   # deduped
    assert "https://other.com/spam/" not in urls              # off-host
    assert all("/wp-content/" not in u for u in urls)         # asset dropped


def test_empty_index_raises():
    empty = b'<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></sitemapindex>'
    with pytest.raises(EmptySitemapError):
        parse_sitemap_index(empty, require_nonempty=True)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_sitemap.py -v`
Expected: FAIL — `ModuleNotFoundError: stratpoint_crawler.sitemap`.

- [ ] **Step 4: Write minimal implementation**

Create `src/stratpoint_crawler/sitemap.py`:
```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_sitemap.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add src/stratpoint_crawler/sitemap.py tests/test_sitemap.py tests/fixtures/sitemap_index.xml tests/fixtures/page-sitemap.xml
git commit -m "feat: parse and filter nested WordPress sitemaps"
```

---

## Task 5: Sitemap fetch orchestration (async, mocked network)

**Files:**
- Modify: `src/stratpoint_crawler/sitemap.py`
- Test: `tests/test_sitemap_fetch.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_sitemap_fetch.py`:
```python
import httpx
import pytest
import respx

from stratpoint_crawler.config import Settings
from stratpoint_crawler.sitemap import discover_page_refs, EmptySitemapError

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sitemap_fetch.py -v`
Expected: FAIL — `ImportError: cannot import name 'discover_page_refs'`.

- [ ] **Step 3: Add the fetch orchestration**

Append to `src/stratpoint_crawler/sitemap.py`:
```python
import httpx


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_sitemap_fetch.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/stratpoint_crawler/sitemap.py tests/test_sitemap_fetch.py
git commit -m "feat: async sitemap discovery with hard-fail on empty"
```

---

## Task 6: Content extraction

**Files:**
- Create: `src/stratpoint_crawler/extract.py`
- Test: `tests/test_extract.py`, `tests/fixtures/page.html`, `tests/fixtures/thin.html`

- [ ] **Step 1: Create HTML fixtures**

Create `tests/fixtures/page.html`:
```html
<!doctype html>
<html><head><title>About Us - Stratpoint</title></head>
<body>
  <header><nav>Home Services Careers</nav></header>
  <main>
    <h1>About Stratpoint</h1>
    <p>We build <a href="https://stratpoint.com/services/">software</a> for clients.</p>
    <p>Second paragraph with enough text to clear the thin-content threshold so the
    extractor treats this as a real page and not a stub. Adding more words here to be safe.</p>
  </main>
  <footer>Copyright 2026 Stratpoint. Social: Twitter Facebook</footer>
  <script>console.log("tracking")</script>
</body></html>
```

Create `tests/fixtures/thin.html`:
```html
<!doctype html>
<html><head><title>Empty - Stratpoint</title></head>
<body><main><h1>Empty</h1><p>Hi.</p></main></body></html>
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_extract.py`:
```python
from pathlib import Path

from stratpoint_crawler.config import Settings
from stratpoint_crawler.extract import extract

FIX = Path(__file__).parent / "fixtures"


def test_extract_strips_chrome_and_keeps_body():
    html = (FIX / "page.html").read_text(encoding="utf-8")
    content = extract(html, url="https://stratpoint.com/about/", settings=Settings())
    assert content.title == "About Us"                 # suffix stripped
    assert "About Stratpoint" in content.markdown
    assert "Home Services Careers" not in content.markdown   # nav gone
    assert "Copyright 2026" not in content.markdown          # footer gone
    assert "tracking" not in content.markdown                # script gone


def test_extract_preserves_inline_links():
    html = (FIX / "page.html").read_text(encoding="utf-8")
    content = extract(html, url="https://stratpoint.com/about/", settings=Settings())
    assert "(https://stratpoint.com/services/)" in content.markdown


def test_extract_hash_is_stable_and_prefixed():
    html = (FIX / "page.html").read_text(encoding="utf-8")
    s = Settings()
    a = extract(html, url="https://stratpoint.com/about/", settings=s)
    b = extract(html, url="https://stratpoint.com/about/", settings=s)
    assert a.content_hash == b.content_hash
    assert a.content_hash.startswith("sha256:")


def test_extract_flags_thin_content():
    html = (FIX / "thin.html").read_text(encoding="utf-8")
    content = extract(html, url="https://stratpoint.com/empty/", settings=Settings())
    assert content.thin is True
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_extract.py -v`
Expected: FAIL — `ModuleNotFoundError: stratpoint_crawler.extract`.

- [ ] **Step 4: Write minimal implementation**

Create `src/stratpoint_crawler/extract.py`:
```python
import hashlib
import re

from markdownify import markdownify as md
from selectolax.parser import HTMLParser

from .config import Settings
from .models import PageContent

_BLANKS = re.compile(r"\n{3,}")


def _title(tree: HTMLParser, settings: Settings) -> str:
    h1 = tree.css_first("h1")
    if h1 and h1.text(strip=True):
        return h1.text(strip=True)
    title_el = tree.css_first("title")
    raw = title_el.text(strip=True) if title_el else ""
    if raw.endswith(settings.title_suffix):
        raw = raw[: -len(settings.title_suffix)]
    return raw.strip()


def _main_html(tree: HTMLParser) -> str:
    for selector in ("main", "article"):
        node = tree.css_first(selector)
        if node is not None:
            return node.html or ""
    # Fallback: largest <div> by text length.
    best = max(
        tree.css("div"),
        key=lambda n: len(n.text(strip=True)),
        default=None,
    )
    if best is not None:
        return best.html or ""
    body = tree.css_first("body")
    return body.html if body else ""


def _normalize(markdown: str) -> str:
    return _BLANKS.sub("\n\n", markdown).strip()


def extract(html: str, *, url: str, settings: Settings) -> PageContent:
    tree = HTMLParser(html)
    title = _title(tree, settings)

    for selector in settings.chrome_selectors:
        for node in tree.css(selector):
            node.decompose()

    body_html = _main_html(tree)
    markdown = _normalize(md(body_html, heading_style="ATX"))
    text_len = len(markdown)
    digest = hashlib.sha256(markdown.encode("utf-8")).hexdigest()

    return PageContent(
        url=url,
        title=title,
        markdown=markdown,
        text_len=text_len,
        content_hash=f"sha256:{digest}",
        thin=text_len < settings.thin_content_min,
    )
```

Note: `_title` runs before chrome stripping so the `<title>` tag is still present (the `script`/`style` selectors do not touch `<title>`, but extracting it first is robust regardless of selector order).

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_extract.py -v`
Expected: PASS (4 passed). If `nav` text leaks because the fixture's `<nav>` is inside `<header>`, both are in `chrome_selectors` so both are removed — confirm the assertions pass.

- [ ] **Step 6: Commit**

```bash
git add src/stratpoint_crawler/extract.py tests/test_extract.py tests/fixtures/page.html tests/fixtures/thin.html
git commit -m "feat: extract clean markdown with chrome stripping and thin-content flag"
```

---

## Task 7: Storage (slug, frontmatter, atomic manifest)

**Files:**
- Create: `src/stratpoint_crawler/storage.py`
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_storage.py`:
```python
import json
from pathlib import Path

from stratpoint_crawler.models import PageContent, PageResult
from stratpoint_crawler.storage import slugify, Writer


def test_slugify_paths():
    assert slugify("https://stratpoint.com/") == "index"
    assert slugify("https://stratpoint.com/about/") == "about"
    assert slugify("https://stratpoint.com/insights/blog/foo/") == "insights__blog__foo"


def test_writer_emits_markdown_with_frontmatter(tmp_path):
    w = Writer(out_dir=tmp_path, crawled_at="2026-06-13T00:00:00Z", save_html=False)
    content = PageContent(url="https://stratpoint.com/about/", title="About",
                          markdown="# About\n\nBody.", text_len=14,
                          content_hash="sha256:abc")
    w.write_page("about", content, raw_html="<html></html>")

    md_file = tmp_path / "pages" / "about.md"
    text = md_file.read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "url: https://stratpoint.com/about/" in text
    assert "content_hash: sha256:abc" in text
    assert "# About" in text
    assert not (tmp_path / "raw_html").exists()    # save_html off


def test_writer_save_html_toggle(tmp_path):
    w = Writer(out_dir=tmp_path, crawled_at="2026-06-13T00:00:00Z", save_html=True)
    content = PageContent(url="https://stratpoint.com/x/", title="X",
                          markdown="x", text_len=1, content_hash="sha256:x")
    w.write_page("x", content, raw_html="<html>raw</html>")
    assert (tmp_path / "raw_html" / "x.html").read_text(encoding="utf-8") == "<html>raw</html>"


def test_writer_index_jsonl_atomic(tmp_path):
    w = Writer(out_dir=tmp_path, crawled_at="2026-06-13T00:00:00Z", save_html=False)
    content = PageContent(url="https://stratpoint.com/about/", title="About",
                          markdown="b", text_len=1, content_hash="sha256:abc")
    results = [
        PageResult(url="https://stratpoint.com/about/", slug="about",
                   status="ok", content=content),
        PageResult(url="https://stratpoint.com/dead/", slug="dead",
                   status="failed", error="timeout"),
    ]
    w.write_index(results)

    lines = (tmp_path / "index.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["slug"] == "about" and first["status"] == "ok"
    assert first["content_hash"] == "sha256:abc"
    second = json.loads(lines[1])
    assert second["status"] == "failed" and second["content_hash"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_storage.py -v`
Expected: FAIL — `ModuleNotFoundError: stratpoint_crawler.storage`.

- [ ] **Step 3: Write minimal implementation**

Create `src/stratpoint_crawler/storage.py`:
```python
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from .models import PageContent, PageResult


def slugify(url: str) -> str:
    path = urlparse(url).path.strip("/")
    if not path:
        return "index"
    return path.replace("/", "__")


def _frontmatter(content: PageContent, crawled_at: str, lastmod: str | None) -> str:
    lines = [
        "---",
        f"url: {content.url}",
        f"title: {content.title}",
        f"crawled_at: {crawled_at}",
        f"lastmod: {lastmod or ''}",
        f"content_hash: {content.content_hash}",
        "---",
        "",
    ]
    return "\n".join(lines)


class Writer:
    def __init__(self, out_dir: Path, crawled_at: str, save_html: bool):
        self.out_dir = Path(out_dir)
        self.crawled_at = crawled_at
        self.save_html = save_html
        self.pages_dir = self.out_dir / "pages"
        self.pages_dir.mkdir(parents=True, exist_ok=True)
        if save_html:
            (self.out_dir / "raw_html").mkdir(parents=True, exist_ok=True)

    def write_page(self, slug: str, content: PageContent,
                   raw_html: str | None = None, lastmod: str | None = None) -> None:
        body = _frontmatter(content, self.crawled_at, lastmod) + content.markdown + "\n"
        (self.pages_dir / f"{slug}.md").write_text(body, encoding="utf-8")
        if self.save_html and raw_html is not None:
            (self.out_dir / "raw_html" / f"{slug}.html").write_text(raw_html, encoding="utf-8")

    def write_index(self, results: list[PageResult]) -> None:
        rows = []
        for r in results:
            c = r.content
            rows.append(json.dumps({
                "url": r.url,
                "title": c.title if c else None,
                "slug": r.slug,
                "lastmod": None,
                "crawled_at": self.crawled_at,
                "content_hash": c.content_hash if c else None,
                "text_len": c.text_len if c else 0,
                "status": r.status,
                "error": r.error,
            }))
        tmp = self.out_dir / "index.jsonl.tmp"
        tmp.write_text("\n".join(rows) + "\n", encoding="utf-8")
        os.replace(tmp, self.out_dir / "index.jsonl")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_storage.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/stratpoint_crawler/storage.py tests/test_storage.py
git commit -m "feat: storage writer for markdown pages and atomic index.jsonl"
```

---

## Task 8: Incremental seam

**Files:**
- Create: `src/stratpoint_crawler/state.py`
- Test: `tests/test_state.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_state.py`:
```python
import json
from pathlib import Path

from stratpoint_crawler.models import PageRef
from stratpoint_crawler.state import load_previous, should_recrawl


def test_load_previous_reads_hashes(tmp_path):
    index = tmp_path / "index.jsonl"
    index.write_text(
        json.dumps({"url": "https://stratpoint.com/a/", "content_hash": "sha256:1"}) + "\n",
        encoding="utf-8",
    )
    prev = load_previous(index)
    assert prev == {"https://stratpoint.com/a/": "sha256:1"}


def test_load_previous_missing_file_returns_empty(tmp_path):
    assert load_previous(tmp_path / "nope.jsonl") == {}


def test_should_recrawl_always_true_today():
    ref = PageRef(url="https://stratpoint.com/a/", lastmod="2025-01-01")
    assert should_recrawl(ref, {"https://stratpoint.com/a/": "sha256:1"}) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_state.py -v`
Expected: FAIL — `ModuleNotFoundError: stratpoint_crawler.state`.

- [ ] **Step 3: Write minimal implementation**

Create `src/stratpoint_crawler/state.py`:
```python
import json
from pathlib import Path

from .models import PageRef


def load_previous(index_path: Path) -> dict[str, str]:
    """Map url -> content_hash from a prior index.jsonl. Empty if absent."""
    index_path = Path(index_path)
    if not index_path.exists():
        return {}
    out: dict[str, str] = {}
    for line in index_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("content_hash"):
            out[row["url"]] = row["content_hash"]
    return out


def should_recrawl(ref: PageRef, previous: dict[str, str]) -> bool:
    """Today: always re-crawl. Incremental logic lands here later (compares
    ref.lastmod / content_hash against `previous`)."""
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_state.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/stratpoint_crawler/state.py tests/test_state.py
git commit -m "feat: add incremental seam (load_previous, should_recrawl no-op)"
```

---

## Task 9: Crawler orchestration (Playwright, async pool)

**Files:**
- Create: `src/stratpoint_crawler/crawler.py`
- Test: `tests/test_crawler.py`

This task isolates the per-page rendering behind a small `Fetcher` protocol so the worker pool, retries, and result accounting are testable without a real browser. The Playwright implementation lives in the same file but is exercised only by the optional integration test in Task 11.

- [ ] **Step 1: Write the failing test (fake fetcher, no browser)**

Create `tests/test_crawler.py`:
```python
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
        return f"<html><body><main><h1>{url}</h1>" + ("x" * 300) + "</main></body></html>"


async def test_crawl_returns_ok_and_failed_results(tmp_path):
    refs = [
        PageRef(url="https://stratpoint.com/about/"),
        PageRef(url="https://stratpoint.com/dead/"),
    ]
    s = Settings(concurrency=2, delay_min=0.0, delay_max=0.0, max_attempts=2)
    results = await crawl(refs, settings=s, fetcher=FakeFetcher(),
                          out_dir=tmp_path, crawled_at="2026-06-13T00:00:00Z")

    by_url = {r.url: r for r in results}
    assert by_url["https://stratpoint.com/about/"].status == "ok"
    assert by_url["https://stratpoint.com/dead/"].status == "failed"
    assert (tmp_path / "pages" / "about.md").exists()
    assert (tmp_path / "index.jsonl").exists()


async def test_crawl_respects_limit(tmp_path):
    refs = [PageRef(url=f"https://stratpoint.com/p{i}/") for i in range(5)]
    s = Settings(concurrency=2, delay_min=0.0, delay_max=0.0)
    results = await crawl(refs, settings=s, fetcher=FakeFetcher(),
                          out_dir=tmp_path, crawled_at="t", limit=2)
    assert len(results) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_crawler.py -v`
Expected: FAIL — `ModuleNotFoundError: stratpoint_crawler.crawler`.

- [ ] **Step 3: Write minimal implementation**

Create `src/stratpoint_crawler/crawler.py`:
```python
import asyncio
import random
from pathlib import Path
from typing import Protocol

from tenacity import retry, stop_after_attempt, wait_exponential

from .config import Settings
from .extract import extract
from .models import PageRef, PageResult
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
            return PageResult(url=ref.url, slug=slug, status="failed", error=str(exc))

    content = extract(html, url=ref.url, settings=settings)
    writer.write_page(slug, content, raw_html=html, lastmod=ref.lastmod)
    return PageResult(url=ref.url, slug=slug, status="ok", content=content)


async def crawl(refs: list[PageRef], *, settings: Settings, fetcher: Fetcher,
                out_dir: Path, crawled_at: str, limit: int | None = None,
                incremental: bool = False) -> list[PageResult]:
    writer = Writer(out_dir=out_dir, crawled_at=crawled_at, save_html=settings_save_html(settings))
    previous = load_previous(Path(out_dir) / "index.jsonl") if incremental else {}

    selected = [r for r in refs if not incremental or should_recrawl(r, previous)]
    if limit is not None:
        selected = selected[:limit]

    sem = asyncio.Semaphore(settings.concurrency)
    tasks = [_fetch_one(fetcher, ref, settings, writer, sem) for ref in selected]
    results = await asyncio.gather(*tasks)
    writer.write_index(results)
    return results


def settings_save_html(settings: Settings) -> bool:
    return getattr(settings, "save_html", False)
```

Note: `save_html` is passed via `Settings` so the CLI can flip it (Task 10 adds the field). `settings_save_html` reads it defensively so this task's tests pass before that field exists.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_crawler.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Add the real Playwright fetcher**

Append to `src/stratpoint_crawler/crawler.py`:
```python
from contextlib import asynccontextmanager


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
```

- [ ] **Step 6: Run the full unit suite (Playwright path not exercised yet)**

Run: `uv run pytest -v`
Expected: PASS — all unit tests green; `PlaywrightFetcher` imports cleanly.

- [ ] **Step 7: Commit**

```bash
git add src/stratpoint_crawler/crawler.py tests/test_crawler.py
git commit -m "feat: async crawl pool with retries + Playwright fetcher"
```

---

## Task 10: CLI entrypoint

**Files:**
- Create: `src/stratpoint_crawler/cli.py`, `src/stratpoint_crawler/__main__.py`
- Modify: `src/stratpoint_crawler/config.py` (add `save_html` field)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Add `save_html` to Settings**

In `src/stratpoint_crawler/config.py`, add this field to `Settings` (after `max_attempts`):
```python
    save_html: bool = False
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_cli.py`:
```python
from stratpoint_crawler.cli import build_parser, settings_from_args


def test_parser_defaults():
    args = build_parser().parse_args([])
    assert args.out == "./data"
    assert args.concurrency == 4
    assert args.incremental is False
    assert args.save_html is False


def test_settings_from_args_overrides():
    args = build_parser().parse_args(
        ["--concurrency", "2", "--delay-min", "0.1", "--delay-max", "0.2", "--save-html"])
    s = settings_from_args(args)
    assert s.concurrency == 2
    assert (s.delay_min, s.delay_max) == (0.1, 0.2)
    assert s.save_html is True
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: stratpoint_crawler.cli`.

- [ ] **Step 4: Write minimal implementation**

Create `src/stratpoint_crawler/cli.py`:
```python
import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .crawler import PlaywrightFetcher, crawl
from .sitemap import discover_page_refs


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="stratpoint-crawler",
                                description="Crawl stratpoint.com into Markdown for RAG.")
    p.add_argument("--out", default="./data", help="Output directory (default ./data)")
    p.add_argument("--limit", type=int, default=None, help="Crawl only first N URLs")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--delay-min", type=float, default=0.5)
    p.add_argument("--delay-max", type=float, default=1.5)
    p.add_argument("--save-html", action="store_true", help="Archive raw HTML")
    p.add_argument("--incremental", action="store_true",
                   help="Skip unchanged pages (no-op until state.py lands)")
    p.add_argument("--verbose", action="store_true")
    return p


def settings_from_args(args: argparse.Namespace) -> Settings:
    return Settings(
        concurrency=args.concurrency,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        save_html=args.save_html,
    )


def _write_report(out_dir: Path, results: list, elapsed: float) -> dict:
    ok = [r for r in results if r.status == "ok"]
    failed = [r for r in results if r.status == "failed"]
    thin = [r for r in ok if r.content and r.content.thin]
    report = {
        "total": len(results),
        "succeeded": len(ok),
        "failed": [{"url": r.url, "error": r.error} for r in failed],
        "thin_content": [r.url for r in thin],
        "elapsed_seconds": round(elapsed, 2),
    }
    (out_dir / "run_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


async def _run(args: argparse.Namespace) -> int:
    settings = settings_from_args(args)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    crawled_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    refs = await discover_page_refs(settings)   # raises EmptySitemapError -> caught in main
    print(f"Discovered {len(refs)} URLs")

    import time
    start = time.monotonic()
    async with PlaywrightFetcher(settings) as fetcher:
        results = await crawl(refs, settings=settings, fetcher=fetcher,
                              out_dir=out_dir, crawled_at=crawled_at,
                              limit=args.limit, incremental=args.incremental)
    report = _write_report(out_dir, results, time.monotonic() - start)
    print(f"Done: {report['succeeded']}/{report['total']} ok, "
          f"{len(report['failed'])} failed, {len(report['thin_content'])} thin")
    return 0


def main() -> int:
    args = build_parser().parse_args()
    from .sitemap import EmptySitemapError
    try:
        return asyncio.run(_run(args))
    except EmptySitemapError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

Create `src/stratpoint_crawler/__main__.py`:
```python
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Run the whole unit suite**

Run: `uv run pytest -v`
Expected: PASS — every test green.

- [ ] **Step 7: Commit**

```bash
git add src/stratpoint_crawler/cli.py src/stratpoint_crawler/__main__.py src/stratpoint_crawler/config.py tests/test_cli.py
git commit -m "feat: CLI entrypoint wiring discovery, crawl, and run report"
```

---

## Task 11: End-to-end smoke test against the live site

**Files:**
- Create: `tests/test_integration.py`
- Create: `README.md`

- [ ] **Step 1: Add an opt-in integration test**

Create `tests/test_integration.py`:
```python
import pytest

from stratpoint_crawler.config import Settings
from stratpoint_crawler.crawler import PlaywrightFetcher, crawl
from stratpoint_crawler.models import PageRef


@pytest.mark.integration
async def test_fetch_real_homepage(tmp_path):
    s = Settings(delay_min=0.0, delay_max=0.0)
    async with PlaywrightFetcher(s) as fetcher:
        results = await crawl([PageRef(url="https://stratpoint.com/")],
                              settings=s, fetcher=fetcher, out_dir=tmp_path,
                              crawled_at="2026-06-13T00:00:00Z")
    assert results[0].status == "ok"
    assert (tmp_path / "pages" / "index.md").stat().st_size > 200
```

Register the marker — add to `pyproject.toml` under `[tool.pytest.ini_options]`:
```toml
markers = ["integration: hits the live network (deselected by default)"]
addopts = "-m 'not integration'"
```

- [ ] **Step 2: Verify the unit suite still excludes integration**

Run: `uv run pytest -v`
Expected: PASS — integration test deselected, all unit tests green.

- [ ] **Step 3: Run the real integration test once (network required)**

Run: `uv run pytest -m integration -v`
Expected: PASS — homepage fetched, `index.md` written and non-trivial. If it fails, this is a real-world signal (selector drift, network) — diagnose with the systematic-debugging skill, don't paper over it.

- [ ] **Step 4: Do a real bounded crawl and eyeball output**

Run:
```bash
uv run stratpoint-crawler --limit 5 --out ./data
```
Expected: prints "Discovered N URLs" then "Done: 5/5 ok ...". Inspect `data/pages/*.md` — frontmatter present, body is real content, nav/footer absent, links intact. Check `data/run_report.json`.

- [ ] **Step 5: Write the README**

Create `README.md`:
```markdown
# Stratpoint.com Crawler

Sitemap-driven Playwright crawler that turns stratpoint.com into per-page
Markdown + an `index.jsonl` manifest for a RAG pipeline.

## Setup
```bash
uv sync
uv run playwright install chromium
```

## Usage
```bash
uv run stratpoint-crawler                 # full crawl into ./data
uv run stratpoint-crawler --limit 5       # smoke test
uv run stratpoint-crawler --save-html     # also archive raw HTML
```

Output:
- `data/pages/<slug>.md` — Markdown with YAML frontmatter
- `data/index.jsonl` — one record per page
- `data/run_report.json` — run summary

## Tests
```bash
uv run pytest                 # unit tests (no network)
uv run pytest -m integration  # live smoke test
```
```

- [ ] **Step 6: Commit**

```bash
git add tests/test_integration.py README.md pyproject.toml
git commit -m "test: live integration smoke test + README"
```

---

## Task 12: Full crawl + verification

- [ ] **Step 1: Run the complete crawl**

Run:
```bash
uv run stratpoint-crawler --out ./data
```
Expected: all discovered URLs processed; "Done: N/N ok" with few/zero failures.

- [ ] **Step 2: Verify the corpus**

Run:
```bash
uv run python -c "import json,glob; rows=[json.loads(l) for l in open('data/index.jsonl',encoding='utf-8')]; print('pages:',len(rows),'ok:',sum(r['status']=='ok' for r in rows),'thin:',sum(1 for r in rows if r['content_hash'] and r['text_len']<200))"
```
Expected: page count matches "Discovered N"; nearly all `ok`; thin-content count is low. Investigate any `failed` URLs and thin pages — they usually mean a selector needs tuning in `config.chrome_selectors` (fix, re-run the relevant unit test, re-crawl).

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "chore: verified full stratpoint.com crawl"
```

---

## Self-Review Notes

- **Spec coverage:** sitemap discovery (T4–5), Playwright render + concurrency 4 + jittered delay (T9), chrome-strip → markdown + links + thin-content + hash (T6), per-page md + frontmatter + atomic jsonl + save-html toggle (T7), incremental seam (T8), CLI flags + run report (T10), hard/soft fail (T5 hard, T9 soft), testing strategy incl. integration (all + T11). All spec sections map to a task.
- **Type consistency:** `PageRef`, `PageContent`, `PageResult` defined once (T3) and used unchanged; `slugify`, `Writer.write_page`, `Writer.write_index`, `extract`, `discover_page_refs`, `crawl`, `should_recrawl`, `load_previous` signatures match across tasks.
- **Known seam:** `save_html` lives on `Settings` (added T10) and is read defensively in T9 so T9's tests pass before T10 — intentional, documented in T9 Step 3.
```
