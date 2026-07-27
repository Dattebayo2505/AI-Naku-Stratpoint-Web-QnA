# Retrieval quality — session decomposition

**Date:** 2026-07-27
**Status:** approved; Session 0 designed (see `2026-07-27-eval-foundation-design.md`)

## Context

Fixing the "who are your leaders?" bug (commit `a7cb482`, `rag/query_rewrite.py`)
surfaced five further problems. The fix that landed — anchoring pronouns to the
company before embedding — addresses one instance of a broader class. This
document groups the five into sessions that can each be completed and verified
independently, and records the one ordering constraint that must not be broken.

The five items are not equally sized, and two of them grew during scoping. What
follows reflects the verified state of the code and corpus, not the original
sketch.

## Verified findings

Checked before sequencing:

- **`VectorStore.query` has no relevance filter** (`rag/store.py:69-88`). It
  returns `k` chunks unconditionally, mapping cosine distance to `score` via
  `1.0 - dist`. Nothing downstream inspects that score. Item #1 confirmed.
- **`RouteResult.query` is dead** (`disambiguation/schemas.py:54`). The field is
  populated with the user input verbatim and never read. Item #2 confirmed.
- **The router short-circuits before retrieval** (`disambiguation/router.py:65-84`):
  harmful, off-topic and greeting inputs set `should_retrieve=False` and never
  reach `retrieve()`. This constrains what an abstention test set can be — see
  the eval design doc.
- **Item #4 is three rules, not one cleanup.** The corpus contains three
  `test-`-prefixed pages (`test-awards-and-recognition`, `b_a_goals__test-whitepaper`,
  `project__test-cloud`) and **17 confirmed `-pdf` twin pairs** where both a base
  page and its `-pdf` variant carry the same content (e.g.
  `universal-approach-to-digital-acceleration-whitepaper` and
  `…-whitepaper-pdf`), on top of the `.png`/`.webp` asset twins originally noted.
- **`test-awards-and-recognition.md` is a real live sitemap page**, faithfully
  mirrored by the crawler. Since `stratpoint_crawl` is stable upstream (CLAUDE.md),
  item #4 is an **ingest-side** filter, not a crawler change.
- **The existing eval cannot support this work.** `rag/eval/run.py` reports only
  binary hit@k over 5 gold cases. None of the five use pronoun or anaphoric
  phrasing, so the harness could never have caught the bug just fixed. Four of the
  five name "Stratpoint" explicitly; the AWS Lambda case does not.

## The one hard ordering rule

**#3 (BGE query prefix) and #1 (relevance floor) must never share a session.**

The BGE prefix shifts every similarity score in the index. A relevance floor is a
threshold calibrated against that score distribution. Doing both at once means
tuning a cutoff against a distribution being moved underneath it, with no way to
attribute an outcome to either change. #1 goes last of the retrieval trio.

The corollary: **Session 1 (corpus hygiene) also cannot overlap Session 4.**
Removing duplicate pages changes which chunks occupy top-k, which moves the same
distribution. Overlapping Session 1 with Session 0 is harmless — building
measurement is not calibrating against it — but overlapping it with Session 4 is not.

## Sessions

| # | Session | Contents | Depends on | Why grouped this way |
|---|---------|----------|-----------|----------------------|
| 0 | Eval foundation | Expand gold set ~5→~41 rows (incl. pronoun paraphrase pairs and an abstention set); report scores and distributions, not just hit/miss; committed baseline | — | Enabler. Without it #3 and #4 are unfalsifiable and #1 is uncalibratable |
| 1 | Corpus hygiene (#4) | Ingest-side slug filter for `test-` pages; collapse `-pdf` twins; collapse asset-extension twins | — | Mechanical, no threshold logic. May run parallel to Session 0 |
| 2 | Query seam (#2) | Make `RouteResult.query` the real seam; move `anchor_entity` onto it; delete the Contact/Location hack | — | Pure refactor, zero scoring impact — schedulable anywhere |
| 3 | BGE prefix (#3) | Query-side instruction prefix | 0 | Isolated by the hard rule. Query-only, so no re-ingest needed |
| 4 | Relevance floor (#1) | Distance cutoff + honest abstention path | 0, 1, 3 | Calibrated last, against the final score distribution |
| 5 | Recency (#5) | Stale-fact handling | — | Needs a design decision before any code; off the critical path |

**Critical path: 0 → 3 → 4.** Sessions 1, 2 and 5 sit off it.

## Rationale on the less obvious groupings

**Why #2 is deliberately alone and cheap.** It is the only item with no scoring
semantics whatsoever. It pays down the hardcoded "Contact / Location"
slug-appending hack at `agent/guardrail_agent.py:253-260`, which is the same bug
just fixed in `query_rewrite.py` — a query needing enrichment before embedding —
patched ad hoc for one topic. Keeping it out of the retrieval sessions means a
failure there cannot be mistaken for a ranking regression.

**Why #5 is not yet a fix session.** It is three different projects wearing one
label: recency weighting at query time, corpus curation (unpublish or annotate
the 2024 CTO announcement), or teaching the prompt to reason about dates. Those
have very different costs and blast radii. It needs a decision before it is
plannable.

**Why Session 0 must not touch the retrieval path.** A baseline produced by code
that also changed retrieval is not a baseline. Session 0 makes no edits to
`retrieve.py`, `store.py`, `embeddings.py` or `query_rewrite.py`.

## Baseline invalidation

Sessions 1 and 3 both invalidate the committed eval baseline — Session 1 by
changing which chunks exist, Session 3 by shifting every score. Each must end by
re-writing the baseline as an explicit, reviewable commit. The baseline header
records the embedding model and a corpus fingerprint so a stale comparison warns
rather than silently misleading.

## Session 0 results

Run on 2026-07-27, k=5, 41 gold cases (25 answerable + 8 pronoun twins + 8
abstention), `BAAI/bge-small-en-v1.5`, corpus fingerprint
`371:3d2ec8e5c8e394ee`.

| Metric | Value |
|--------|-------|
| hit@5 | 0.88 |
| MRR | 0.630 |
| hit@5, `entity-named` axis | 0.92 |
| hit@5, `pronoun` axis | 0.75 |
| Gold-chunk scores (n=29) | min 0.727 / p25 0.797 / median 0.833 |
| Abstention top-1 scores (n=8) | median 0.751 / p75 0.787 / max 0.804 |
| **Abstention cases above the gold median** | **0/8** |

### Separation verdict: buildable, but not free

By the go/no-go metric the distributions separate — no abstention question
scores above the median gold chunk. Session 4's relevance floor is therefore
**not** disqualified.

The tails do overlap, though, and that changes the design. `abstain_max` (0.804)
sits *above* `gold_p25` (0.797), so the "cutoff between abstain_max and
gold_p25" window the plan anticipated does not exist. Eight of 29 correctly
retrieved gold chunks score below `abstain_max`. The cost curve:

| Cutoff | Correctly abstains | Falsely abstains |
|--------|--------------------|------------------|
| 0.750 | 4/8 | 2/29 |
| 0.780 | 6/8 | 5/29 |
| 0.800 | 7/8 | 8/29 |
| 0.805 | 8/8 | 8/29 |
| 0.830 | 8/8 | 13/29 |

Catching every abstention case costs ~28% false abstention on answerable
questions. **Session 4 should not aim for a single global cutoff tuned to catch
all abstentions.** A conservative floor near 0.75 buys half the abstentions for
7% false-abstention, and the rest of the gap needs a different mechanism than a
distance threshold. Re-check these numbers after Session 3 — the BGE query
prefix shifts every score and this whole table moves.

### Retrieval findings

**Pronoun axis lags entity-named by 17 points** (0.75 vs 0.92), and two
paraphrase pairs diverge — the twin misses while the entity-named sibling hits:

- `co-privacy -> co-privacy-p` — the gold `privacy-notice` page falls to rank 7,
  beaten by four sibling privacy notices (events, recruitment, holiday-giveaway,
  softcon).
- `svc-qa -> svc-qa-p`.

**Near-duplicate pages dominate top-k.** This is the strongest evidence for
Session 1. Seven of the 25 answerable gold slugs have a same-title twin, most
with byte-identical length: `qa-playbook`/`qa-playbook-slides`,
`webinar-need-for-speed`/`need-for-speed-video`,
`machine-learning-masterclass-video`/`webinar-machine-learning-masterclass`,
plus four `universal-approach-to-digital-acceleration-*` variants. Single pages
also occupy several top-k slots at once — `2024__11__11__embarking-on-the-data-journey`
takes 3 of the top 8 on `blog-data-tips`, and `stratpoint-outsystems-development`
takes 3 of 8 on `blog-outsystems-fast`. Both are misses caused by duplicate
occupancy rather than by bad ranking.

**Gold-label correction made during triage.** `svc-qa`/`svc-qa-p` were
originally labelled against `qaservices1`, the stale half of a twin pair;
re-pointed to the canonical, newer, longer `qaservices` (`/qaservices/`). The
other four remaining misses were triaged as fair and kept.
