# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A **RAG chatbot for `stratpoint.com`**, managed with **uv**, Python 3.13. The site is crawled into a per-page Markdown corpus, which feeds a retrieval pipeline and an agentic chatbot served over an API with a Streamlit chat UI.

Only the **crawler** (`stratpoint_crawl`) is implemented so far; the chatbot components exist as scaffolded subpackages under `src/stratpoint_rag/` and are built out incrementally.

## Repository layout

Two top-level packages with a deliberate ownership split:

```
src/
├── stratpoint_crawl/    # LIVE — sitemap-driven Playwright crawler → data/pages/*.md + index.jsonl
└── stratpoint_rag/      # the chatbot (one subpackage per component)
    ├── rag/             # planned — chunking, embeddings, vector store, retrieval
    ├── prompts/         # planned — prompt engineering: system prompts, few-shot, CoT templates
    ├── disambiguation/  # planned — ambiguous-input detection; clarify intent before tool calls
    ├── guardrails/      # planned — input/output guardrails
    ├── agent/           # planned — ReAct agent orchestrating retrieval + tools
    ├── api/             # planned — HTTP API endpoint (e.g. FastAPI)
    ├── ui/              # planned — Streamlit chat UI
    └── evaluation/      # planned — retrieval / answer-quality evals
```

**`stratpoint_crawl` is maintained and run by the repo owner** — treat it as a stable upstream: don't restructure it or move it back under `stratpoint_rag`, and only touch it when the task is explicitly about crawling/extraction. Chatbot work goes in `stratpoint_rag`; when implementing a planned component, put it in its subpackage (its `__init__.py` docstring states the intended scope). New chatbot features (prompting, disambiguation, guardrails, agent behavior, API, UI) map onto these folders — not necessarily 1-to-1, but the boundaries above are the default. The only coupling between the two packages is the crawled corpus on disk: `data/pages/*.md` + `data/index.jsonl` (see the corpus invariant below) — `stratpoint_rag` must not import from `stratpoint_crawl`.

## Commands

This project is uv-managed, but works with plain pip/venv too — pick one toolchain.

**With uv (preferred):**

```bash
uv sync                              # install deps from uv.lock
uv run playwright install chromium   # one-time browser download (required)

uv run pytest                        # unit suite (no network; integration deselected by default)
uv run pytest tests/test_extract.py::test_extract_divi_layout_drops_related_posts -v   # single test
uv run pytest -m integration         # opt-in live test against stratpoint.com

uv run stratpoint-crawler            # full crawl into ./data
uv run stratpoint-crawler --limit 5  # smoke test on first 5 sitemap URLs
uv run stratpoint-crawler --save-html --out ./data
uv run stratpoint-crawler --help
```

**Without uv (pip + venv):**

```bash
python -m venv .venv
# activate: source .venv/bin/activate   (macOS/Linux)
#           .venv\Scripts\Activate.ps1   (Windows PowerShell)

pip install -e .                              # installs deps + the stratpoint-crawler console script
pip install pytest pytest-asyncio respx       # dev deps (live in [dependency-groups], which pip ignores; or: pip install --group dev  on pip >= 25.1)
playwright install chromium                   # one-time browser download (required)

pytest                                        # unit suite
pytest -m integration                         # live test
stratpoint-crawler --limit 5                  # or: python -m stratpoint_crawl --limit 5
```

The console script (`stratpoint-crawler`) and `python -m stratpoint_crawl` are equivalent — both call `stratpoint_crawl/cli.py:main`. Use the latter if the script isn't on PATH after install.

`pytest` config lives in `pyproject.toml`: `asyncio_mode = "auto"` (async tests need no `@pytest.mark.asyncio`) and `addopts = "-m 'not integration'"` (the live test is excluded unless explicitly selected).

## Crawler architecture (`stratpoint_crawl`)

Pipeline, wired together in `stratpoint_crawl/cli.py:_run`:

```
sitemap.discover_page_refs → crawl(fetcher) → extract → storage.Writer → index.jsonl + run_report.json
```

1. **`sitemap.py`** — fetches the nested WordPress sitemap (`sitemap_index.xml` → child sitemaps) over `httpx`, returns `list[PageRef]`. URL discovery is sitemap-only by design; there is **no link-spider**. If the sitemap is unreachable or yields zero URLs it raises `EmptySitemapError` (hard fail) rather than falling back to crawling — the error message names the not-yet-built `--seed-url` mode as the intended seam.
2. **`crawler.py`** — async worker pool (`asyncio.Semaphore(concurrency)`, jittered politeness delay, `tenacity` retries). Per-page fetch failures are recorded as `status="failed"` and the crawl continues (soft fail); only setup problems abort.
3. **`extract.py`** — `selectolax` strips chrome, `markdownify` converts the body, SHA-256 hashes it.
4. **`storage.py`** — writes `data/pages/<slug>.md` (YAML frontmatter + body) and an atomically-written `data/index.jsonl`.

### Key design decisions (read before editing)

- **The `Fetcher` Protocol is the testability seam.** `crawl()` takes any object with `async fetch(url) -> str`. Tests inject a `FakeFetcher`; production uses `PlaywrightFetcher` (an async context manager owning one headless Chromium, one `BrowserContext` per page). This is why the entire crawl loop — concurrency, retries, result accounting — is unit-tested **without a browser**. Keep it that way: don't make `crawl()` import or construct Playwright directly.

- **Site-specific extraction tuning lives in `config.py`, not logic.** When extraction grabs the wrong content, the fix is almost always a selector in `Settings.chrome_selectors` (CSS removed before conversion) or `consent_button_selectors` (best-effort dismissal). Examples already there: Divi related-posts (`.et_pb_posts`, `.et_pb_post`) and the CookieYes banner (`.cky-*`). Add selectors; don't add branching to `extract.py`.

- **`extract._main_html` does NOT take the first `<article>`.** stratpoint.com is Divi (WordPress) with no `<main>` and ~10 small `<article>` related-post cards. The heuristic is: `<main>` → a *single* `<article>` (only when exactly one exists) → `<body>` after chrome stripping. Taking the first `<article>` was a real bug; `tests/fixtures/divi.html` guards against the regression.

- **Incremental mode is live (`--incremental`, `--force`).** `state.should_recrawl()` skips a page when its sitemap `lastmod` equals the value in the prior `index.jsonl`; `--force` recrawls everything. `crawl()` returns a `CrawlSummary` (`results`/`skipped`/`removed`), **not** a list. Un-recrawled pages (skipped *and* removed-from-sitemap) are carried forward verbatim as `status="skipped"`, so the manifest always describes the full corpus. **Corpus invariant: a page is present when `status` is `ok` OR `skipped`; detect change via `content_hash`, never `status == "ok"`.** Removed pages are carried forward and reported, never deleted. Downstream RAG ingestion must honor this: index pages with status `ok`/`skipped`, and use `content_hash` to decide what to re-embed.

- **`crawled_at` is stamped once in `cli._run` and threaded through** `crawl → Writer` and `_write_report`, never read from the clock inside `storage`/`state`. This keeps those modules deterministic under test.

- **Run freshness in `run_report.json` is success-gated.** It carries `run_finished_at` (= this run's `crawled_at`) and `last_successful_scrape`, which `state.resolve_last_successful_scrape` advances **only when ≥1 page is `status="ok"`** — an all-skip/all-fail run carries the prior value forward (read back via `state.load_last_successful_scrape`). Freshness means a *successful* scrape, not a run merely happening; don't re-derive it from `max(crawled_at)` (failed runs stamp `crawled_at` too).

### Validating extraction changes

Unit tests use HTML/XML fixtures in `tests/fixtures/` (`page.html`, `divi.html`, `thin.html`, the sitemap XMLs) — fast, offline. But selector changes must also be checked against the **live** site, since the fixtures can't capture JS-injected markup. After touching `extract.py` or the selectors, run a bounded real crawl (`--limit 5 --save-html`) and eyeball `data/pages/*.md`: confirm the body is real article text, nav/footer/cookie content is gone, and links survive. The `run_report.json` `thin_content` list flags pages under 200 chars — a quick signal that extraction missed a body.

### Verifying incremental

The **first** `--incremental` run after a full crawl recrawls everything — a pre-feature manifest stores `lastmod` as `null`, so that run seeds it; the *next* run skips unchanged pages. Fast check without a ~5-min crawl: `stratpoint-crawler --incremental --force --limit 3` (proves `--force` overrides the skip). The incremental path is also unit-tested offline by seeding a prior `index.jsonl` and asserting `FakeFetcher.calls` never includes skipped URLs.

## Deployment target

The project will be hosted on a **Linux container (LXC) managed by Proxmox**: the LXC is the environment that will run the RAG code and the **dockerized local model**. When working on deployment, model serving, or environment config (the `api`/`ui` subpackages, Docker setup, `.env` handling), assume this target rather than cloud hosting.

**`.envexample`** at the repo root is the committed template for `.env` (which is gitignored): container credentials and endpoint — `LCX_ROOT_USERNAME`/`LCX_ROOT_PASSWORD`, `NON_ROOT_USERNAME`/`NON_ROOT_PASSWORD`, `PUBLIC_IP_ADDRESS`, `PORT`. Keep new environment variables mirrored in `.envexample` (values blank) so other groups can reference it. Note the existing `LCX_` spelling (not `LXC_`) — match it.

### Session logs

- **`docs/general-log.md`** — Claude-maintained, **non-technical** log (report/presentation material: milestones, decisions, artifacts — not bug fixes, merges, or test runs). When the user asks to "update the log" (or similar), the project-level **`update-log`** skill (`.claude/skills/update-log/`) governs how to write it. Follow that skill; don't hand-roll a different format.
- **`docs/INPUTHERE_self-log.md`** — the user's personal log. **Do not edit it** — it's theirs (the `INPUTHERE_` prefix is a placeholder they may rename).
