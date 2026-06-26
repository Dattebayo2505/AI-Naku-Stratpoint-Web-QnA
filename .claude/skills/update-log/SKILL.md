---
name: update-log
description: >-
  Use when the user asks to update, refresh, append to, log, jot down, or "write up"
  what was done this session into the project log / general log / session log — e.g.
  "update the log", "log what we did", "add today's progress to the log", "update
  general-log", "put this in the log for the report". Maintains docs/general-log.md as
  a NON-TECHNICAL, report- and presentation-oriented record of milestones, decisions,
  and the documents/artifacts produced each session; it deliberately leaves out
  technical mechanics. Reach for it whenever the user means "the project log" here, even
  if they don't name the file. This is the narrative session log ONLY — do NOT use it
  for application/console logging or log levels, git log, CHANGELOG release notes, README
  edits, debugging output/log files, or the user's personal docs/INPUTHERE_self-log.md
  (that file is never edited by this skill).
---

# Update Log

## Why this log exists

`docs/general-log.md` is **not** an engineering changelog. It is raw material for a
later **report or presentation** about this project. The reader is a teammate (or your
future self) who will turn entries into a slide or a paragraph — someone who cares about
*what we set out to do, what approach we chose and why, what we produced, and what's
still open* — and who does **not** care how the code works.

Write every entry for that reader. If a line wouldn't help someone write a report or
build a slide, it doesn't belong.

## When the user asks to update the log

1. Look back over the session (and recent git/file activity if helpful) for the
   **story of progress**: the phases of work, the decisions made, the documents and
   deliverables produced, and anything left undecided.
2. Translate that into the include/exclude rules below — this is the heart of the skill.
3. Read the current `docs/general-log.md`. If today already has an entry, refresh it;
   otherwise add a new dated entry at the **top** (newest first).
4. Keep it skimmable: a one-line summary of the session, then short grouped bullets.
   Reference each artifact by its path so a report-writer can open or cite it.
5. Resolve ambiguity by **asking** (see "When you're unsure").

## What to include

These are the things a report or presentation would actually use:

- **Phases of work, anchored to their artifacts.** "Brainstormed the design and captured
  it in `docs/.../X-design.md`", "wrote the implementation plan in `docs/.../Y.md`",
  "drafted the survey in `survey.md`". The artifact path matters — it's what the
  report-writer cites or screenshots.
- **Decisions and the reasoning behind them.** "Chose a sitemap-driven approach over a
  link-follower because the site publishes a full sitemap." Decisions are the backbone
  of a methodology section.
- **Deliverables and headline outcomes.** "Produced a 371-page corpus", "ran the survey
  with 42 respondents". Include the numbers a slide would show.
- **Open questions / things to decide.** Great material for a "next steps" slide.

## What to leave out

These are real work, but they're mechanics — invisible in a report:

- Bug fixes, debugging, error messages.
- Branch/merge/commit/git mechanics.
- Test runs, pass/fail counts, coverage.
- Refactors, selector tweaks, config fiddling, and other low-level implementation detail.
- Tooling minutiae — unless a tool choice is itself a headline decision worth presenting.

The test: *"fixed the extraction bug where related-posts leaked in"* is mechanics — leave
it out. *"Decided to extract only main page content, dropping site navigation and
related-content widgets"* is a decision a report would mention — keep it (phrased as the
decision, not the bug).

## When you're unsure — ask

The line between "decision worth presenting" and "technical mechanics" is a judgment
call, and you'll sometimes guess wrong. When an item is borderline:

- Make your best call and **include or drop it** so the log stays usable, then
- **Tell the user what you did and ask.** For example: *"I left out the database migration
  and the CI fixes as too technical, and I folded the three extraction tweaks into one
  line 'decided to keep only main page text.' Want me to add any of those back, or cut
  anything I kept?"*

Don't silently bury borderline-technical content, and don't pad the log with mechanics
just to be safe. A short, honest "here's what I included and dropped — your call" keeps
the log aligned with what the user actually wants to present.

## Entry format

Newest entry on top. Use this shape (adapt the groupings to the session — not every
session has all three):

```markdown
## YYYY-MM-DD — <one-line summary of the session>

**What we did**
- <phase / decision, citing the artifact: `path/to/doc.md`>
- ...

**What we produced**
- <deliverable + headline number, citing the artifact>

**Open / to decide**
- <unresolved question for a future session / next-steps slide>
```

## Example: turning a session into a log entry

**Raw session activity (what actually happened):**
> Brainstormed the crawler in `specs/2026-06-13-design.md`. Argued about sitemap vs
> link-crawl, picked sitemap. Wrote a 12-task plan in `plans/2026-06-13.md`. Coded it,
> fixed a Divi `<article>` bug and a cookie-banner leak, merged `feat/crawler` to master,
> all 27 tests green. Crawl produced 371 pages, 8 came out near-empty.

**Good log entry (non-technical, report-ready):**
```markdown
## 2026-06-14 — Designed and built the stratpoint.com crawler

**What we did**
- Brainstormed the crawler's design and captured the agreed approach in a spec
  (`docs/superpowers/specs/2026-06-13-stratpoint-crawler-design.md`).
- Chose a sitemap-driven approach over a link-following crawler, because the site
  publishes a complete sitemap — simpler and more reliable coverage.
- Turned the spec into a step-by-step implementation plan
  (`docs/superpowers/plans/2026-06-13-stratpoint-crawler.md`) and built against it.

**What we produced**
- A 371-page Markdown corpus of the site, ready for the RAG pipeline.

**Open / to decide**
- 8 pages came out near-empty (download forms and internal test pages) — keep or drop
  them before embedding?
```

Notice what was dropped: the Divi bug, the cookie-banner fix, the branch merge, and the
test count. Notice what was kept and reframed: the *decision* about approach, the
*artifacts*, the *deliverable*, and the *open question*.

## The companion self-log — do not touch

`docs/INPUTHERE_self-log.md` is the user's personal log. **Never edit it.** This skill
only ever writes `docs/general-log.md`.
