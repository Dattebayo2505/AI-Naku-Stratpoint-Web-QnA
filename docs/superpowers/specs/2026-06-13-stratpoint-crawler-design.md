# Stratpoint.com Web Crawler — Design Spec

**Date:** 2026-06-13
**Status:** Approved for planning
**Target site:** https://stratpoint.com (public, no auth)
**Purpose:** Crawl stratpoint.com and produce clean per-page Markdown for a downstream RAG (retrieval-augmented generation) pipeline.

---

## 1. Goal & Scope

Build a Playwright-based crawler, managed with `uv`, that:

1. Discovers all content URLs via the site's WordPress sitemap (`sitemap_index.xml`).
2. Renders each page with headless Chromium.
3. Extracts the main content body (stripping nav/header/footer/chrome) and converts it to Markdown, preserving inline hyperlinks for citation.
4. Writes one Markdown file per page plus a machine-readable `index.jsonl` manifest.

**Approach chosen:** Sitemap-driven discovery + Playwright rendering + Markdown extraction (Approach 1 from brainstorming). No recursive link-spidering.

**Operational mode:** One-shot crawl now, structured so an incremental mode can be added later without restructuring. Incremental wiring (`state.py`, `--incremental`, `content_hash`) is present today but is a no-op (always full crawl).

**Out of scope (YAGNI):** link-walking/BFS spider, authentication, structured field extraction, the incremental skip logic itself, scheduling.

---

## 2. Site Facts (verified 2026-06-13)

- Server-rendered HTML (Playwright still used per user request; also future-proofs against JS sections).
- `robots.txt` permits all crawlers, no disallowed paths, no crawl-delay.
- Sitemap index at `http://stratpoint.com/sitemap_index.xml` (typical nested WordPress structure).
- Top-level sections: Home, Services, Portfolio, Partners, Solutions, Insights (Blogs/E-books/Media/Newsletters/Webinars), Careers, About, Contact Us.

---

## 3. Project Layout

```
AA_STAI100_Stratpoint_RAG/
├── pyproject.toml            # uv-managed, declares deps & entry point
├── README.md
├── .python-version           # pinned via `uv python pin`
├── src/
│   └── stratpoint_crawler/
│       ├── __init__.py
│       ├── __main__.py       # `python -m stratpoint_crawler`
│       ├── cli.py            # argparse/typer entrypoint
│       ├── config.py         # tunables (concurrency, delay, paths, selectors, UA)
│       ├── sitemap.py        # fetch + parse sitemap_index.xml -> URL list
│       ├── crawler.py        # Playwright orchestration, concurrency, polite delay
│       ├── extract.py        # HTML -> main content -> markdown
│       ├── storage.py        # write .md files + atomic index.jsonl manifest
│       └── state.py          # incremental seam (stub today)
├── tests/
│   ├── test_sitemap.py
│   ├── test_extract.py
│   ├── test_storage.py
│   ├── test_state.py
│   └── fixtures/             # sample HTML + XML for offline tests
└── data/                     # gitignored; output goes here
    ├── pages/                # per-page markdown
    ├── raw_html/             # optional raw HTML archive (--save-html)
    └── index.jsonl
```

`state.py` exists from day one so the import surface does not shift when incremental mode is implemented.

---

## 4. Dependencies (via `uv add`)

| Package | Purpose |
|---|---|
| `playwright` | Headless Chromium page rendering (`uv run playwright install chromium` once) |
| `httpx` | Fetch sitemap XML (lighter than a browser for static XML) |
| `lxml` | Fast XML parsing of sitemaps |
| `selectolax` | Fast HTML parsing + CSS-selector chrome stripping |
| `markdownify` | HTML -> Markdown conversion |
| `tenacity` | Retry with backoff for transient fetch failures |
| `pydantic` | Typed config + metadata schema |

**Dev:** `pytest`, `pytest-asyncio`, `respx` (httpx mocking).

---

## 5. Components

### 5.1 `sitemap.py` — URL discovery

WordPress sitemaps are nested: `sitemap_index.xml` -> child sitemaps (`page-sitemap.xml`, `post-sitemap.xml`, ...) -> page URLs with `<lastmod>`.

**Flow:**
1. Fetch `sitemap_index.xml` with `httpx`.
2. Parse with `lxml`; collect child sitemap URLs.
3. Fetch each child sitemap; collect `<loc>` + `<lastmod>` pairs.
4. Filter to host `stratpoint.com`; drop non-content (`/wp-content/`, image-only sitemaps, undesired binary types).
5. Return deduplicated `list[PageRef]` where `PageRef = {url, lastmod}`.

**Fallback:** If the sitemap is unreachable or empty: log a clear error and exit non-zero, naming the (not-yet-built) `--seed-url` link-crawl mode as the future seam. **No silent fallback to spidering.**

**Why httpx not Playwright:** sitemap is static XML; a browser would be wasteful. Fully testable offline with canned XML fixtures.

### 5.2 `crawler.py` — Playwright orchestration

Takes `list[PageRef]`, drives the fetch loop.

**Concurrency & politeness (decided values):**
- Concurrency: **4** simultaneous renders (`asyncio.Semaphore(4)`).
- Per-request delay: **1.0s jittered to 0.5–1.5s**, applied per worker before each navigation (avoids synchronized bursts).
- Single headless Chromium; each of the 4 workers gets its own `BrowserContext` (isolated cookies/cache).
- Default Chromium User-Agent (no custom UA, per user decision).

**Per-page sequence:**
1. Acquire semaphore slot -> apply jittered delay.
2. `page.goto(url, wait_until="domcontentloaded", timeout=30s)`.
3. Best-effort dismiss cookie/consent banner if a known selector is present (non-fatal).
4. `page.content()` -> rendered HTML.
5. Hand HTML + url + lastmod to `extract.py`, then `storage.py`.
6. Release slot.

**Resilience:**
- `tenacity`: up to 3 attempts on timeout/navigation errors, exponential backoff.
- A page failing all retries is recorded (url + error); crawl continues. One bad page never kills the run.
- End-of-run summary: N succeeded, M failed, failed URLs.

All tunables live in `config.py` (concurrency, delay bounds, timeout) for one-edit changes and test injection.

### 5.3 `extract.py` — content extraction

Turns rendered HTML into clean RAG-ready Markdown. Goal: keep the article body, drop site chrome.

**Pipeline:**
1. Parse HTML with `selectolax`.
2. Strip chrome by selector: `<nav>`, `<header>`, `<footer>`, `<script>`, `<style>`, `<noscript>`, cookie/consent banners, social-share widgets, lead-form blocks. Selector list lives in `config.py` (tunable without touching logic).
3. Locate main content: prefer `<main>` or `<article>`; fall back to largest text-density `<div>`. Heuristic isolated in one testable function.
4. Extract title: prefer `<h1>`, fall back to `<title>` minus the " - Stratpoint" suffix.
5. Convert to Markdown with `markdownify` (headings, lists, links, tables preserved; images as `![alt](src)`). **Inline hyperlinks kept** for citation.
6. Normalize: collapse 3+ blank lines to 2, strip leading/trailing whitespace, drop empty artifacts.

**Output:** `PageContent = {url, title, markdown, text_len, content_hash}`. `content_hash` = SHA-256 of normalized markdown (the incremental change key; computed now, acted on later).

**Quality guardrail:** markdown < 200 chars after stripping -> flagged "thin content" in the run summary (does not fail the page).

### 5.4 `storage.py` — output writing

**Output layout (format A):**
```
data/
├── pages/<slug>.md
├── raw_html/<slug>.html     # only when --save-html
└── index.jsonl
```

**Slug derivation:** from URL path; separators -> `__`. Example: `https://stratpoint.com/insights/blog/foo/` -> `insights__blog__foo.md`. Homepage `/` -> `index.md`. Keeps flat `pages/` collision-free while staying readable.

**Markdown file contents:** YAML frontmatter (`url`, `title`, `crawled_at`, `lastmod`, `content_hash`) + body. Self-describing; loaders can read or ignore the frontmatter.

**`index.jsonl`** — one record per page:
```json
{"url": "...", "title": "...", "slug": "insights__blog__foo",
 "lastmod": "2025-06-01", "crawled_at": "2026-06-13T...Z",
 "content_hash": "sha256:...", "text_len": 4213, "status": "ok"}
```
Built in memory, written atomically at end so a mid-crash run never leaves a half-written manifest.

`crawled_at` is passed in from the CLI (stamped once at startup) so `storage` and `state` stay deterministic under test.

### 5.5 `state.py` — incremental seam

Ships as a thin module today, operating against the existing `index.jsonl`:
- `load_previous(index_path) -> dict[url, content_hash]`
- `should_recrawl(page_ref, previous) -> bool` — **today always returns `True`** (full crawl); later compares sitemap `lastmod` and/or `content_hash` to skip unchanged pages.

The crawler already calls `should_recrawl()` in its loop. Going incremental = implement this one function + honor `--incremental`. No restructuring.

---

## 6. CLI

`uv run stratpoint-crawler [OPTIONS]` (registered via `[project.scripts]`; works after `uv sync`).

| Option | Default | Meaning |
|---|---|---|
| `--out PATH` | `./data` | Output directory |
| `--limit N` | none | Crawl only first N URLs (smoke testing) |
| `--concurrency N` | 4 | Concurrent renders |
| `--delay-min` / `--delay-max` | 0.5 / 1.5 | Jitter bounds (seconds) |
| `--save-html` | off | Archive raw HTML to `raw_html/` |
| `--incremental` | off | Skip unchanged pages (wired, no-op until `state.py` lands) |
| `--verbose` | off | Debug logging |

---

## 7. Error Handling

**Fail loud at boundaries (exit non-zero):**
- Sitemap unreachable/empty.
- Output dir not writable.
- Playwright/Chromium not installed (message: "run `uv run playwright install chromium`").

**Soft-fail (log + continue):**
- Individual page timeout.
- Thin-content extraction.
- Banner-dismiss miss.

**Run summary** printed at end and written to `data/run_report.json`: total, succeeded, failed (URLs + errors), thin-content flagged, elapsed time.

---

## 8. Testing Strategy

Implemented test-first (test-driven-development skill). No live network in the unit suite.

- `test_sitemap.py` — canned nested XML fixtures -> parsed `PageRef` list, host filtering, dedup, empty-sitemap error.
- `test_extract.py` — saved HTML fixtures -> chrome stripped, title parsed, body + links survive, thin-content detection, stable `content_hash`.
- `test_storage.py` — temp dir -> slug derivation, frontmatter, atomic `index.jsonl`, `--save-html` toggle.
- `test_state.py` — `should_recrawl` returns `True` today; `load_previous` parses an existing index.
- One optional `@pytest.mark.integration` test hits a single real page (skipped by default / when offline).

---

## 9. Future Seams (documented, not built)

- **Incremental mode:** implement `should_recrawl()` + honor `--incremental`; keys off `lastmod` / `content_hash` already captured.
- **Link-crawl fallback:** `--seed-url` BFS mode, named in the sitemap-failure error path.
- **One-hop hybrid:** sitemap seed + single link-hop, if a corpus gap check reveals missing pages.
