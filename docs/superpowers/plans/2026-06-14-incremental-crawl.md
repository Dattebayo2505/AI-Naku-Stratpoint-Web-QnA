# Incremental Crawl Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `--incremental` skip re-fetching pages whose sitemap `<lastmod>` is unchanged since the last run, with a `--force` escape hatch — instead of recrawling all 371 pages every time.

**Architecture:** All changes land on the existing `models.py` / `storage.py` / `state.py` / `crawler.py` / `cli.py` seam built for this; no new modules. `lastmod` (already on `PageRef`) becomes the skip gate, gets persisted into `index.jsonl`, and `state.should_recrawl` compares current vs stored `lastmod`. Skipped pages are carried forward verbatim so the manifest always describes the full corpus.

**Tech Stack:** Python 3.13, uv, pydantic, pytest + pytest-asyncio (offline unit tests).

**Spec:** `docs/superpowers/specs/2026-06-14-incremental-crawl-design.md`

## Global Constraints

- Python 3.13, uv-managed. Run everything via `uv run`.
- Unit tests are offline; `asyncio_mode = "auto"` (async tests need no marker).
- **Corpus invariant:** a page is present in the corpus when `status` is `"ok"` **or** `"skipped"`. Change detection downstream uses `content_hash`, never `status == "ok"`.
- Default (non-`--incremental`) behavior must stay identical to today: all pages crawled, manifest fully rewritten.
- Commit after each task.

---

## File Structure

| File | Change |
|---|---|
| `src/stratpoint_crawler/models.py` | Add `lastmod` to `PageResult`; add `CrawlSummary` |
| `src/stratpoint_crawler/storage.py` | Persist real `lastmod`; `write_index` accepts `carried_records` |
| `src/stratpoint_crawler/state.py` | `load_previous` returns full records; `should_recrawl(ref, prev_record, force)` |
| `src/stratpoint_crawler/crawler.py` | `_fetch_one` sets `lastmod`; `crawl` selects/carries/removes, returns `CrawlSummary`, takes `force` |
| `src/stratpoint_crawler/cli.py` | `--force` flag; report gains `crawled`/`skipped`/`removed`; reads `summary` |
| `tests/test_models.py` | `PageResult.lastmod`, `CrawlSummary` |
| `tests/test_storage.py` | `lastmod` persisted; carried records written as `skipped` |
| `tests/test_state.py` | rewrite for new `load_previous` shape + `should_recrawl` cases |
| `tests/test_crawler.py` | read `summary.results`; incremental skip/carry/remove |
| `tests/test_cli.py` | `--force` parsing; `_write_report` fields |
| `tests/test_integration.py` | read `summary.results` (return-type change) |

---

## Task 1: Models — `PageResult.lastmod` + `CrawlSummary`

**Files:**
- Modify: `src/stratpoint_crawler/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: `PageResult(url, slug, status, error=None, content=None, lastmod=None)`; `CrawlSummary(results: list[PageResult], skipped: int, removed: list[str])`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_models.py`:
```python
def test_pageresult_carries_lastmod():
    r = PageResult(url="https://x/", slug="x", status="ok", lastmod="2025-06-01")
    assert r.lastmod == "2025-06-01"
    assert PageResult(url="https://x/", slug="x", status="failed").lastmod is None


def test_crawl_summary_holds_results_skipped_removed():
    from stratpoint_crawler.models import CrawlSummary
    s = CrawlSummary(results=[], skipped=3, removed=["https://x/gone/"])
    assert s.skipped == 3
    assert s.removed == ["https://x/gone/"]
    assert s.results == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL — `PageResult` has no `lastmod` / cannot import `CrawlSummary`.

- [ ] **Step 3: Write minimal implementation**

In `src/stratpoint_crawler/models.py`, add `lastmod` to `PageResult` and append `CrawlSummary`:
```python
class PageResult(BaseModel):
    url: str
    slug: str
    status: str
    error: str | None = None
    content: PageContent | None = None
    lastmod: str | None = None


class CrawlSummary(BaseModel):
    results: list[PageResult] = []
    skipped: int = 0
    removed: list[str] = []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS (all model tests green).

- [ ] **Step 5: Commit**

```bash
git add src/stratpoint_crawler/models.py tests/test_models.py
git commit -m "feat: add PageResult.lastmod and CrawlSummary"
```

---

## Task 2: Storage — persist `lastmod`, carry skipped records

**Files:**
- Modify: `src/stratpoint_crawler/storage.py:47-64` (`write_index`)
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: `PageResult.lastmod` (Task 1).
- Produces: `Writer.write_index(results: list[PageResult], carried_records: list[dict] | None = None)`. Fresh records use `r.lastmod`; each carried record is written verbatim except `status` forced to `"skipped"`.

- [ ] **Step 1: Write the failing test**

In `tests/test_storage.py`, update `test_writer_index_jsonl_atomic` to give the ok result a `lastmod` and assert it persists, and add a carried-records test. Replace the existing `test_writer_index_jsonl_atomic` with:
```python
def test_writer_index_persists_lastmod_and_status(tmp_path):
    w = Writer(out_dir=tmp_path, crawled_at="2026-06-14T00:00:00Z", save_html=False)
    content = PageContent(url="https://stratpoint.com/about/", title="About",
                          markdown="b", text_len=1, content_hash="sha256:abc")
    results = [
        PageResult(url="https://stratpoint.com/about/", slug="about",
                   status="ok", content=content, lastmod="2025-05-20"),
        PageResult(url="https://stratpoint.com/dead/", slug="dead",
                   status="failed", error="timeout"),
    ]
    w.write_index(results)

    lines = (tmp_path / "index.jsonl").read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    assert first["lastmod"] == "2025-05-20"      # no longer hardcoded None
    assert first["status"] == "ok"
    second = json.loads(lines[1])
    assert second["status"] == "failed" and second["content_hash"] is None


def test_writer_index_carries_skipped_records(tmp_path):
    w = Writer(out_dir=tmp_path, crawled_at="2026-06-14T00:00:00Z", save_html=False)
    content = PageContent(url="https://stratpoint.com/new/", title="New",
                          markdown="n", text_len=1, content_hash="sha256:new")
    fresh = [PageResult(url="https://stratpoint.com/new/", slug="new",
                        status="ok", content=content, lastmod="2026-06-01")]
    carried = [{
        "url": "https://stratpoint.com/about/", "title": "About", "slug": "about",
        "lastmod": "2025-05-20", "crawled_at": "2026-06-10T00:00:00Z",
        "content_hash": "sha256:abc", "text_len": 42, "status": "ok", "error": None,
    }]
    w.write_index(fresh, carried_records=carried)

    rows = [json.loads(l) for l in (tmp_path / "index.jsonl").read_text(encoding="utf-8").splitlines()]
    by_url = {r["url"]: r for r in rows}
    assert by_url["https://stratpoint.com/new/"]["status"] == "ok"
    carried_row = by_url["https://stratpoint.com/about/"]
    assert carried_row["status"] == "skipped"            # forced
    assert carried_row["content_hash"] == "sha256:abc"   # preserved
    assert carried_row["crawled_at"] == "2026-06-10T00:00:00Z"  # original kept
    assert carried_row["lastmod"] == "2025-05-20"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_storage.py -v`
Expected: FAIL — `lastmod` is `None` (hardcoded) and `write_index` takes no `carried_records`.

- [ ] **Step 3: Write minimal implementation**

Replace `write_index` in `src/stratpoint_crawler/storage.py`:
```python
    def write_index(self, results: list[PageResult],
                    carried_records: list[dict] | None = None) -> None:
        carried_records = carried_records or []
        rows = []
        for r in results:
            c = r.content
            rows.append(json.dumps({
                "url": r.url,
                "title": c.title if c else None,
                "slug": r.slug,
                "lastmod": r.lastmod,
                "crawled_at": self.crawled_at,
                "content_hash": c.content_hash if c else None,
                "text_len": c.text_len if c else 0,
                "status": r.status,
                "error": r.error,
            }))
        for rec in carried_records:
            carried = dict(rec)
            carried["status"] = "skipped"
            rows.append(json.dumps(carried))
        tmp = self.out_dir / "index.jsonl.tmp"
        tmp.write_text("\n".join(rows) + "\n", encoding="utf-8")
        os.replace(tmp, self.out_dir / "index.jsonl")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_storage.py -v`
Expected: PASS (all storage tests green).

- [ ] **Step 5: Commit**

```bash
git add src/stratpoint_crawler/storage.py tests/test_storage.py
git commit -m "feat: persist lastmod and carry skipped records in index.jsonl"
```

---

## Task 3: State — real `load_previous` + `should_recrawl`

**Files:**
- Modify: `src/stratpoint_crawler/state.py` (whole file)
- Test: `tests/test_state.py` (rewrite)

**Interfaces:**
- Produces: `load_previous(index_path) -> dict[str, dict]` (url → full prior record); `should_recrawl(ref: PageRef, prev_record: dict | None, force: bool = False) -> bool`.

- [ ] **Step 1: Write the failing test (rewrite the file)**

Replace the entire contents of `tests/test_state.py`:
```python
import json

from stratpoint_crawler.models import PageRef
from stratpoint_crawler.state import load_previous, should_recrawl


def _rec(url, lastmod="2025-01-01", status="ok"):
    return {"url": url, "lastmod": lastmod, "status": status, "content_hash": "sha256:1"}


def test_load_previous_returns_full_records_keyed_by_url(tmp_path):
    index = tmp_path / "index.jsonl"
    index.write_text(json.dumps(_rec("https://stratpoint.com/a/")) + "\n", encoding="utf-8")
    prev = load_previous(index)
    assert prev["https://stratpoint.com/a/"]["lastmod"] == "2025-01-01"
    assert prev["https://stratpoint.com/a/"]["status"] == "ok"


def test_load_previous_missing_file_returns_empty(tmp_path):
    assert load_previous(tmp_path / "nope.jsonl") == {}


def test_recrawl_new_page():
    ref = PageRef(url="https://stratpoint.com/a/", lastmod="2025-01-01")
    assert should_recrawl(ref, None) is True


def test_skip_when_lastmod_unchanged():
    ref = PageRef(url="https://stratpoint.com/a/", lastmod="2025-01-01")
    assert should_recrawl(ref, _rec(ref.url, "2025-01-01")) is False


def test_recrawl_when_lastmod_changed():
    ref = PageRef(url="https://stratpoint.com/a/", lastmod="2025-06-01")
    assert should_recrawl(ref, _rec(ref.url, "2025-01-01")) is True


def test_recrawl_when_prev_lastmod_missing():
    ref = PageRef(url="https://stratpoint.com/a/", lastmod="2025-01-01")
    assert should_recrawl(ref, _rec(ref.url, lastmod=None)) is True


def test_recrawl_when_sitemap_lastmod_missing():
    ref = PageRef(url="https://stratpoint.com/a/", lastmod=None)
    assert should_recrawl(ref, _rec(ref.url, "2025-01-01")) is True


def test_recrawl_when_prev_failed():
    ref = PageRef(url="https://stratpoint.com/a/", lastmod="2025-01-01")
    assert should_recrawl(ref, _rec(ref.url, "2025-01-01", status="failed")) is True


def test_force_always_recrawls():
    ref = PageRef(url="https://stratpoint.com/a/", lastmod="2025-01-01")
    assert should_recrawl(ref, _rec(ref.url, "2025-01-01"), force=True) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_state.py -v`
Expected: FAIL — `load_previous` returns hashes not records; `should_recrawl` ignores `lastmod`/`force`.

- [ ] **Step 3: Write minimal implementation**

Replace the entire contents of `src/stratpoint_crawler/state.py`:
```python
import json
from pathlib import Path

from .models import PageRef


def load_previous(index_path: Path) -> dict[str, dict]:
    """Map url -> the full prior index.jsonl record. Empty if the file is absent."""
    index_path = Path(index_path)
    if not index_path.exists():
        return {}
    out: dict[str, dict] = {}
    for line in index_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        url = row.get("url")
        if url:
            out[url] = row
    return out


def should_recrawl(ref: PageRef, prev_record: dict | None, force: bool = False) -> bool:
    """Decide whether a page must be fetched this run.

    Skip (return False) only for a page that previously succeeded and whose
    sitemap lastmod is unchanged. Anything else — forced, new, previously
    failed, or missing a lastmod on either side — is recrawled, because we
    must never skip on an absent or unreliable signal.
    """
    if force or prev_record is None:
        return True
    if prev_record.get("status") not in ("ok", "skipped"):
        return True
    prev_lastmod = prev_record.get("lastmod")
    if not prev_lastmod or not ref.lastmod:
        return True
    return ref.lastmod != prev_lastmod
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_state.py -v`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add src/stratpoint_crawler/state.py tests/test_state.py
git commit -m "feat: lastmod-based should_recrawl and full-record load_previous"
```

---

## Task 4: Crawler — incremental selection, carry/remove, `CrawlSummary`

**Files:**
- Modify: `src/stratpoint_crawler/crawler.py` (`_fetch_one`, `crawl`)
- Test: `tests/test_crawler.py`
- Modify: `tests/test_integration.py` (return-type update)

**Interfaces:**
- Consumes: `should_recrawl(ref, prev_record, force)` and `load_previous` (Task 3); `write_index(results, carried_records)` (Task 2); `CrawlSummary` and `PageResult.lastmod` (Task 1).
- Produces: `crawl(refs, *, settings, fetcher, out_dir, crawled_at, limit=None, incremental=False, force=False) -> CrawlSummary`. `summary.results` holds only pages fetched this run (ok/failed).

- [ ] **Step 1: Write the failing test**

Replace the two existing tests in `tests/test_crawler.py` (`test_crawl_returns_ok_and_failed_results`, `test_crawl_respects_limit`) so they read `summary.results`, and add incremental coverage. The file's imports and `FakeFetcher` stay; replace from the first `async def test_` onward with:
```python
import json


async def test_crawl_returns_ok_and_failed_results(tmp_path):
    refs = [
        PageRef(url="https://stratpoint.com/about/"),
        PageRef(url="https://stratpoint.com/dead/"),
    ]
    s = Settings(concurrency=2, delay_min=0.0, delay_max=0.0, max_attempts=2)
    summary = await crawl(refs, settings=s, fetcher=FakeFetcher(),
                          out_dir=tmp_path, crawled_at="2026-06-14T00:00:00Z")
    by_url = {r.url: r for r in summary.results}
    assert by_url["https://stratpoint.com/about/"].status == "ok"
    assert by_url["https://stratpoint.com/dead/"].status == "failed"
    assert (tmp_path / "pages" / "about.md").exists()
    assert (tmp_path / "index.jsonl").exists()
    assert summary.skipped == 0 and summary.removed == []


async def test_crawl_respects_limit(tmp_path):
    refs = [PageRef(url=f"https://stratpoint.com/p{i}/") for i in range(5)]
    s = Settings(concurrency=2, delay_min=0.0, delay_max=0.0)
    summary = await crawl(refs, settings=s, fetcher=FakeFetcher(),
                          out_dir=tmp_path, crawled_at="t", limit=2)
    assert len(summary.results) == 2


def _seed_index(tmp_path, records):
    (tmp_path / "index.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


async def test_incremental_skips_unchanged_and_crawls_changed(tmp_path):
    # Prior manifest: /a unchanged (will skip), /b changed (will recrawl).
    _seed_index(tmp_path, [
        {"url": "https://stratpoint.com/a/", "slug": "a", "lastmod": "2025-01-01",
         "status": "ok", "content_hash": "sha256:a", "title": "A", "text_len": 9,
         "crawled_at": "2026-06-10T00:00:00Z", "error": None},
        {"url": "https://stratpoint.com/b/", "slug": "b", "lastmod": "2025-01-01",
         "status": "ok", "content_hash": "sha256:b", "title": "B", "text_len": 9,
         "crawled_at": "2026-06-10T00:00:00Z", "error": None},
    ])
    refs = [
        PageRef(url="https://stratpoint.com/a/", lastmod="2025-01-01"),   # unchanged -> skip
        PageRef(url="https://stratpoint.com/b/", lastmod="2025-09-01"),   # changed -> recrawl
        PageRef(url="https://stratpoint.com/c/", lastmod="2025-09-01"),   # new -> crawl
    ]
    fetcher = FakeFetcher()
    s = Settings(concurrency=2, delay_min=0.0, delay_max=0.0)
    summary = await crawl(refs, settings=s, fetcher=fetcher, out_dir=tmp_path,
                          crawled_at="2026-06-14T00:00:00Z", incremental=True)

    fetched = set(fetcher.calls)
    assert "https://stratpoint.com/a/" not in fetched          # skipped: never fetched
    assert "https://stratpoint.com/b/" in fetched              # changed: fetched
    assert "https://stratpoint.com/c/" in fetched              # new: fetched
    assert summary.skipped == 1
    assert summary.removed == []

    rows = {json.loads(l)["url"]: json.loads(l)
            for l in (tmp_path / "index.jsonl").read_text(encoding="utf-8").splitlines()}
    assert set(rows) == {f"https://stratpoint.com/{p}/" for p in "abc"}   # full corpus
    assert rows["https://stratpoint.com/a/"]["status"] == "skipped"
    assert rows["https://stratpoint.com/a/"]["content_hash"] == "sha256:a"  # carried


async def test_incremental_reports_removed_pages(tmp_path):
    _seed_index(tmp_path, [
        {"url": "https://stratpoint.com/gone/", "slug": "gone", "lastmod": "2025-01-01",
         "status": "ok", "content_hash": "sha256:g", "title": "G", "text_len": 9,
         "crawled_at": "2026-06-10T00:00:00Z", "error": None},
    ])
    refs = [PageRef(url="https://stratpoint.com/a/", lastmod="2025-01-01")]   # /gone not in sitemap
    s = Settings(concurrency=2, delay_min=0.0, delay_max=0.0)
    summary = await crawl(refs, settings=s, fetcher=FakeFetcher(), out_dir=tmp_path,
                          crawled_at="2026-06-14T00:00:00Z", incremental=True)
    assert summary.removed == ["https://stratpoint.com/gone/"]
    assert summary.skipped == 0   # removed pages are not counted as skipped

    # removed page is still carried forward into the manifest (non-destructive)
    rows = {json.loads(l)["url"]: json.loads(l)
            for l in (tmp_path / "index.jsonl").read_text(encoding="utf-8").splitlines()}
    assert rows["https://stratpoint.com/gone/"]["status"] == "skipped"
    assert rows["https://stratpoint.com/a/"]["status"] == "ok"


async def test_force_recrawls_despite_unchanged_lastmod(tmp_path):
    _seed_index(tmp_path, [
        {"url": "https://stratpoint.com/a/", "slug": "a", "lastmod": "2025-01-01",
         "status": "ok", "content_hash": "sha256:a", "title": "A", "text_len": 9,
         "crawled_at": "2026-06-10T00:00:00Z", "error": None},
    ])
    refs = [PageRef(url="https://stratpoint.com/a/", lastmod="2025-01-01")]
    fetcher = FakeFetcher()
    s = Settings(concurrency=2, delay_min=0.0, delay_max=0.0)
    summary = await crawl(refs, settings=s, fetcher=fetcher, out_dir=tmp_path,
                          crawled_at="2026-06-14T00:00:00Z", incremental=True, force=True)
    assert "https://stratpoint.com/a/" in fetcher.calls   # forced refetch
    assert summary.skipped == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_crawler.py -v`
Expected: FAIL — `crawl` returns a list (no `.results`/`.skipped`) and ignores `incremental`/`force` semantics.

- [ ] **Step 3: Update `_fetch_one` to set `lastmod`**

In `src/stratpoint_crawler/crawler.py`, set `lastmod` on both returns of `_fetch_one`:
```python
        try:
            html = await _attempt()
        except Exception as exc:  # noqa: BLE001 - record failure and continue the crawl
            return PageResult(url=ref.url, slug=slug, status="failed",
                              error=str(exc), lastmod=ref.lastmod)

    content = extract(html, url=ref.url, settings=settings)
    writer.write_page(slug, content, raw_html=html, lastmod=ref.lastmod)
    return PageResult(url=ref.url, slug=slug, status="ok",
                      content=content, lastmod=ref.lastmod)
```

- [ ] **Step 4: Rewrite `crawl` and update imports**

Change the import line in `src/stratpoint_crawler/crawler.py`:
```python
from .models import CrawlSummary, PageRef, PageResult
```
Replace the whole `crawl` function:
```python
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
```

(Note: combining `--limit` with `--incremental` truncates the *selected* set, so pages past the limit are neither crawled nor carried unless already in the prior manifest — `--limit` stays a smoke-test tool, not a production path.)

- [ ] **Step 5: Update the integration test for the new return type**

In `tests/test_integration.py`, change the call + assertion:
```python
    summary = await crawl([PageRef(url="https://stratpoint.com/")],
                          settings=s, fetcher=fetcher, out_dir=tmp_path,
                          crawled_at="2026-06-14T00:00:00Z")
    assert summary.results[0].status == "ok"
    assert (tmp_path / "pages" / "index.md").stat().st_size > 200
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_crawler.py -v`
Expected: PASS (6 passed). Then `uv run pytest` — full unit suite green (integration still deselected).

- [ ] **Step 7: Commit**

```bash
git add src/stratpoint_crawler/crawler.py tests/test_crawler.py tests/test_integration.py
git commit -m "feat: incremental selection, carry-forward, removed detection, CrawlSummary"
```

---

## Task 5: CLI — `--force` flag and richer report

**Files:**
- Modify: `src/stratpoint_crawler/cli.py` (`build_parser`, `_write_report`, `_run`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `crawl(...) -> CrawlSummary` (Task 4).
- Produces: `--force` CLI flag; `run_report.json` with keys `crawled`, `succeeded`, `skipped`, `removed`, `failed`, `thin_content`, `elapsed_seconds`.

- [ ] **Step 1: Write the failing test**

In `tests/test_cli.py`, add to `test_parser_defaults` the assertion `assert args.force is False`, and append:
```python
def test_force_flag_parses():
    args = build_parser().parse_args(["--incremental", "--force"])
    assert args.incremental is True and args.force is True


def test_write_report_counts(tmp_path):
    from stratpoint_crawler.cli import _write_report
    from stratpoint_crawler.models import CrawlSummary, PageContent, PageResult

    thin_c = PageContent(url="https://x/t/", title="T", markdown="hi",
                         text_len=2, content_hash="sha256:t", thin=True)
    summary = CrawlSummary(
        results=[
            PageResult(url="https://x/ok/", slug="ok", status="ok",
                       content=PageContent(url="https://x/ok/", title="O", markdown="body",
                                           text_len=4, content_hash="sha256:o")),
            PageResult(url="https://x/t/", slug="t", status="ok", content=thin_c),
            PageResult(url="https://x/d/", slug="d", status="failed", error="boom"),
        ],
        skipped=5,
        removed=["https://x/gone/"],
    )
    report = _write_report(tmp_path, summary, 1.23)
    assert report["crawled"] == 3
    assert report["succeeded"] == 2
    assert report["skipped"] == 5
    assert report["removed"] == ["https://x/gone/"]
    assert len(report["failed"]) == 1
    assert report["thin_content"] == ["https://x/t/"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — no `--force`; `_write_report` takes `results` not `summary` and lacks the new keys.

- [ ] **Step 3: Add the `--force` flag**

In `build_parser`, after the `--incremental` argument:
```python
    p.add_argument("--force", action="store_true",
                   help="Recrawl every page even with --incremental")
```

- [ ] **Step 4: Rewrite `_write_report` to read the summary**

Replace `_write_report` in `src/stratpoint_crawler/cli.py`:
```python
def _write_report(out_dir: Path, summary, elapsed: float) -> dict:
    results = summary.results
    ok = [r for r in results if r.status == "ok"]
    failed = [r for r in results if r.status == "failed"]
    thin = [r for r in ok if r.content and r.content.thin]
    report = {
        "crawled": len(results),
        "succeeded": len(ok),
        "skipped": summary.skipped,
        "removed": summary.removed,
        "failed": [{"url": r.url, "error": r.error} for r in failed],
        "thin_content": [r.url for r in thin],
        "elapsed_seconds": round(elapsed, 2),
    }
    (out_dir / "run_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
```

- [ ] **Step 5: Thread `force` and the summary through `_run`**

In `_run`, replace the crawl call and the final print:
```python
    async with PlaywrightFetcher(settings) as fetcher:
        summary = await crawl(
            refs, settings=settings, fetcher=fetcher, out_dir=out_dir,
            crawled_at=crawled_at, limit=args.limit,
            incremental=args.incremental, force=args.force,
        )
    report = _write_report(out_dir, summary, time.monotonic() - start)
    print(f"Done: {report['succeeded']} ok, {report['skipped']} skipped, "
          f"{len(report['removed'])} removed, {len(report['failed'])} failed")
    return 0
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest -v`
Expected: PASS — full unit suite green.

- [ ] **Step 7: Commit**

```bash
git add src/stratpoint_crawler/cli.py tests/test_cli.py
git commit -m "feat: --force flag and crawled/skipped/removed run report"
```

---

## Task 6: End-to-end verification against the live site

**Files:** none (verification only)

- [ ] **Step 1: Establish a baseline crawl (populates `lastmod`)**

Run:
```bash
uv run stratpoint-crawler --out ./data
```
Expected: `Discovered 371 URLs` then `Done: ~371 ok, 0 skipped, 0 removed, ...`. This run writes real `lastmod` into `data/index.jsonl` (the pre-existing manifest had `lastmod: null`). ~5 min — the slow part is the baseline.

- [ ] **Step 2: Confirm `lastmod` is now persisted**

Run:
```bash
uv run python -c "import json; r=[json.loads(l) for l in open('data/index.jsonl',encoding='utf-8')]; print('with lastmod:', sum(1 for x in r if x['lastmod']), '/', len(r))"
```
Expected: nearly all rows have a non-null `lastmod` (a few may legitimately lack one in the sitemap).

- [ ] **Step 3: Run incrementally and confirm pages are skipped**

Run:
```bash
uv run stratpoint-crawler --incremental --out ./data
```
Expected: `Done: 0 ok, ~371 skipped, 0 removed, 0 failed` (give or take any pages whose `lastmod` genuinely changed between the two runs). The run finishes in seconds — it fetches the sitemap and skips every unchanged page without launching page renders.

- [ ] **Step 4: Confirm `--force` overrides the skip**

Run:
```bash
uv run stratpoint-crawler --incremental --force --out ./data
```
Expected: `Done: ~371 ok, 0 skipped, ...` — `--force` recrawls everything despite matching `lastmod`. (Slow again, like Step 1.) If satisfied, you can Ctrl-C after confirming the count line shows it is crawling rather than skipping.

- [ ] **Step 5: Final commit (no code change; marks verification done)**

```bash
git commit --allow-empty -m "chore: verified incremental crawl end-to-end (skip + force)"
```

---

## Self-Review Notes

- **Spec coverage:** lastmod signal (T3 `should_recrawl`), persist lastmod (T2), `status="skipped"` + carry-forward (T2/T4), `load_previous` full records (T3), crawl selection + removed (T4), `CrawlSummary` (T1/T4), `--force` (T5), report `crawled`/`skipped`/`removed` (T5), first-run-recrawls-all (verified T6 Step 1→3), backward-compat default path (T4 `else: selected = list(refs)`; T6). All spec sections map to a task.
- **Type consistency:** `should_recrawl(ref, prev_record, force)`, `load_previous -> dict[str, dict]`, `write_index(results, carried_records)`, `crawl(...) -> CrawlSummary`, `PageResult.lastmod`, `CrawlSummary.results/skipped/removed` are used identically across tasks and tests.
- **Default-path safety:** non-incremental `crawl` sets `previous={}`, so `carried`/`removed` are empty and `write_index` behaves as before — except `lastmod` is now populated (intended, backward-compatible per spec §8).
