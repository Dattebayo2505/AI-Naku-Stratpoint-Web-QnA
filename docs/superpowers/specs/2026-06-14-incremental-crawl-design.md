# Incremental Crawl — Design Spec

**Date:** 2026-06-14
**Status:** Approved for planning
**Depends on:** the existing crawler (`docs/superpowers/specs/2026-06-13-stratpoint-crawler-design.md`)

---

## 1. Goal & Scope

Make `--incremental` real: skip re-fetching pages that haven't changed since the last run,
instead of re-crawling all 371 pages every time. The change signal is the sitemap's
`<lastmod>`; a `--force` flag provides a full-reconciliation escape hatch.

**In scope:** lastmod-based skip, carry-forward manifest, removed-page reporting, `--force`,
report counts.

**Out of scope (YAGNI):** conditional HTTP (`If-Modified-Since`/`ETag`), content-hash-based
skipping (a hash needs a fetch, so it can't save crawl work), pruning/deleting `.md` files for
removed pages, "recrawl anything older than N days".

**Design principle:** all changes land on the existing `state.py` / `storage.py` / `crawler.py`
seam that was built for this. No new modules, no restructuring of the crawl loop.

---

## 2. Change signal & rationale

The only signal that avoids the fetch entirely is the sitemap `<lastmod>`: compare the current
sitemap value for a URL against the value stored from the last run; equal → skip without fetching.

`lastmod` can lie (WordPress doesn't always bump it on an edit). That risk is accepted and
mitigated by `--force`, which recrawls everything on demand to reconcile. Content-hash comparison
is *not* used as a skip gate because computing the hash requires fetching and extracting the page —
it saves no crawl work. (The hash remains in the manifest for downstream change detection; see §3.)

---

## 3. Manifest changes (`models.py`, `storage.py`)

### `PageResult` gains `lastmod`

Add `lastmod: str | None = None` so the sitemap value flows
`PageRef → _fetch_one → PageResult → write_index`. Today `write_index` hardcodes
`"lastmod": None`; that is the immediate bug this fixes.

### `status` gains `"skipped"`

`status` values become:

| value | meaning |
|---|---|
| `ok` | fetched this run; content present |
| `skipped` | not fetched this run; carried forward from a prior run, content unchanged |
| `failed` | fetch failed this run; no content |

**Corpus invariant:** a page is present in the corpus when `status` is `ok` **or** `skipped`.
Downstream consumers that want "which pages changed" must compare `content_hash`, not filter on
`status == "ok"` (that would wrongly drop unchanged pages).

### `write_index(results, carried_records=[])`

Second argument: the verbatim prior `index.jsonl` records for skipped pages. They are written
alongside fresh records, unchanged except `status` set to `"skipped"`. Carried records keep their
original `crawled_at`, `content_hash`, `text_len`, `title`, and `lastmod` — accurate, because those
reflect the last time the page was actually fetched. The manifest is still written atomically
(build in memory → `os.replace`).

### `index.jsonl` record shape (unchanged keys, `lastmod` now populated)

```json
{"url": "...", "title": "...", "slug": "...", "lastmod": "2025-06-01",
 "crawled_at": "2026-06-14T...Z", "content_hash": "sha256:...",
 "text_len": 4213, "status": "ok", "error": null}
```

---

## 4. State logic (`state.py`)

### `load_previous(index_path) -> dict[str, dict]`

Changes from `{url: content_hash}` to `{url: <full prior record dict>}`, so skipped pages can be
carried forward intact. Missing file → `{}` (unchanged behavior).

### `should_recrawl(ref, prev_record, force=False) -> bool`

```
if force or prev_record is None:                     return True   # forced, or new page
if prev_record["status"] not in ("ok", "skipped"):  return True   # retry prior failures
if not prev_record["lastmod"] or not ref.lastmod:   return True   # no usable signal → recrawl
return ref.lastmod != prev_record["lastmod"]                       # changed → recrawl, else skip
```

- New pages (not in prior manifest) are always crawled.
- Previously-failed pages are always retried (they have no content to keep).
- Missing `lastmod` on either side → recrawl (safe default — never skip on absent signal).

**First-run consequence:** existing `index.jsonl` files (from full crawls) store `lastmod: None`
for every page, so the first `--incremental` run after this ships recrawls everything and thereby
establishes the `lastmod` baseline. Subsequent runs skip unchanged pages. This is intended.

---

## 5. Crawl flow (`crawler.py`)

```python
previous = load_previous(Path(out_dir) / "index.jsonl") if incremental else {}

if incremental:
    selected = [r for r in refs if should_recrawl(r, previous.get(r.url), force)]
else:
    selected = list(refs)
if limit is not None:
    selected = selected[:limit]

results = await asyncio.gather(*[_fetch_one(...) for r in selected])   # ok/failed; .md written

selected_urls = {r.url for r in selected}
sitemap_urls = {r.url for r in refs}
carried = [previous[u] for u in previous if u not in selected_urls]    # every un-recrawled page
removed = [u for u in previous if u not in sitemap_urls]               # gone from sitemap
skipped = sum(1 for u in previous if u not in selected_urls and u in sitemap_urls)

writer.write_index(results, carried_records=carried)
return CrawlSummary(results=results, skipped=skipped, removed=removed)
```

- `_fetch_one` sets `PageResult.lastmod = ref.lastmod` for both `ok` and `failed` results.
- **Every page not re-crawled this run is carried forward** (`carried`) and written as `status="skipped"`,
  so the manifest stays a complete description of the corpus and no `.md` file is orphaned. This includes
  *removed* pages (those gone from the sitemap) — they are carried forward like any other un-recrawled page.
- `removed` pages are **additionally** listed in `removed` for reporting (and their `.md` is never deleted),
  so you have a signal that they're gone from the live site even though their record/file are retained.
- `skipped` counts only un-recrawled pages still present in the sitemap — it excludes removed pages, so the
  report distinguishes "unchanged" from "gone."
- `removed`/`carried`/`previous` are only meaningful in incremental mode; in full mode `previous`
  is empty, so `carried`/`removed` are empty and behavior is identical to today.

### `crawl()` returns a `CrawlSummary`

`crawl()` changes its return type from `list[PageResult]` to a small dataclass so the CLI can build
the report without re-loading the just-overwritten manifest:

```python
@dataclass
class CrawlSummary:
    results: list[PageResult]   # fresh ok/failed from THIS run (not skipped/carried)
    skipped: int                # count of pages carried forward unchanged
    removed: list[str]          # URLs in old manifest no longer in sitemap
```

This is a deliberate breaking change to `crawl()`'s signature; the existing `test_crawler.py`
assertions and `cli._run` are updated to read `summary.results`. `results` holds only pages fetched
this run, so the report's `succeeded`/`failed`/`thin_content` counts are computed from it as today.

---

## 6. CLI & report (`cli.py`)

- **`--force`** (new): recrawl everything even under `--incremental`. Without `--incremental`,
  `--force` is a no-op (full crawl is already the default).
- **`--incremental`**: now functional (previously a wired no-op).
- **`run_report.json`** gains:
  - `crawled` — count fetched this run (`ok` + `failed` from this run)
  - `skipped` — count + URLs carried forward
  - `removed` — URLs in the old manifest no longer in the sitemap
  - existing `succeeded`, `failed`, `thin_content`, `elapsed_seconds`
- End-of-run line, e.g.: `Done: 12 crawled, 357 skipped, 2 removed, 0 failed`.

---

## 7. Testing (TDD, all offline)

- **`test_state.py`** — rewrite for the new `load_previous` return shape (full record dict).
  `should_recrawl` cases: new page → True; unchanged `lastmod` → False; changed `lastmod` → True;
  missing prior `lastmod` → True; missing sitemap `lastmod` → True; prior `status="failed"` → True;
  `force=True` → True.
- **`test_storage.py`** — `lastmod` persisted (not `None`); `carried_records` written verbatim with
  `status="skipped"`; manifest still atomic.
- **`test_crawler.py`** — update existing assertions to read `summary.results` (return type change).
  New incremental run seeded with a prior `index.jsonl`: unchanged pages are skipped (assert the
  `FakeFetcher.calls` list never contains them), changed pages re-fetched, a new page fetched, a
  removed page reported in `summary.removed`, manifest describes the full corpus (crawled + skipped).
  Full-mode run unaffected (no `previous`; `summary.skipped == 0`, `summary.removed == []`).
- **`test_cli.py`** — `--force` parses; `--force` forces full recrawl even with `--incremental`.

---

## 8. Backward compatibility

- Default behavior (no `--incremental`) is unchanged: every page recrawled, manifest fully rewritten.
- The `index.jsonl` schema is unchanged in shape; only `lastmod` flips from always-`None` to populated,
  and `status` may now be `"skipped"`. Existing consumers reading `url`/`content_hash`/`status=="ok"`
  keep working, except they must now also treat `"skipped"` as present (documented invariant, §3).
