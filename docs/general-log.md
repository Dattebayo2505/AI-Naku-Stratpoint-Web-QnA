# General Log

Non-technical, report- and presentation-oriented record of what was done on this
project, **maintained by Claude Code via the `update-log` skill**.

This is raw material for a later report or presentation — milestones, decisions, and the
documents/artifacts produced. It deliberately leaves out technical mechanics (bug fixes,
branch merges, test runs). When the user asks to "update the log," the `update-log` skill
refreshes the newest entry below.

---

## 2026-06-26 — Added incremental refresh to the crawler

**What we did**
- Set out to make re-crawls cheap: refresh only the pages that changed since the last run
  instead of re-scraping the whole site every time.
- Chose the sitemap's "last modified" date as the change signal — a page is skipped when
  its date is unchanged — with a manual full-refresh override for the cases where the site
  doesn't update that date reliably. Rationale and approach captured in the design spec
  (`docs/superpowers/specs/2026-06-14-incremental-crawl-design.md`).
- Decided to keep the corpus complete and non-destructive: pages that disappear from the
  site are retained and flagged rather than deleted, so nothing is lost silently.
- Wrote a step-by-step implementation plan
  (`docs/superpowers/plans/2026-06-14-incremental-crawl.md`) and built against it.

**What we produced**
- An incremental refresh mode: re-running over the 371-page corpus with nothing changed
  now completes in ~1.5 seconds (skipping all 371 pages), versus the ~5-minute full crawl.
  Verified end-to-end against the live site, including the full-refresh override.

**Open / to decide**
- Removed pages are currently kept and flagged; decide later whether to prune them from the
  corpus so retrieval never serves pages that no longer exist on the site.

---

## 2026-06-14 — Designed and built the stratpoint.com crawler

**What we did**
- Brainstormed the crawler's design through a guided Q&A and captured the agreed approach
  in a design spec (`docs/superpowers/specs/2026-06-13-stratpoint-crawler-design.md`).
- Chose a sitemap-driven approach (read the site's published sitemap) over a
  link-following crawler, because stratpoint.com exposes a complete WordPress sitemap —
  simpler and more reliable coverage.
- Decided to extract only each page's main content, dropping site navigation, footers,
  and related-content widgets, so the corpus is clean article text.
- Turned the spec into a step-by-step implementation plan
  (`docs/superpowers/plans/2026-06-13-stratpoint-crawler.md`) and built against it.

**What we produced**
- A 371-page Markdown corpus of stratpoint.com (one file per page) plus a manifest,
  ready to feed the RAG pipeline.
- Project documentation: `README.md` and `CLAUDE.md`.

**Open / to decide**
- 8 pages came out near-empty (e-book download forms and internal test pages). Decide
  whether to keep or drop them from the corpus before embedding.

---
