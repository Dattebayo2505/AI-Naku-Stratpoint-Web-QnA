# Session 0 — eval foundation

**Date:** 2026-07-27
**Status:** approved, ready for implementation planning
**Roadmap context:** `2026-07-27-retrieval-quality-roadmap.md` (this is Session 0 of 5)

## Purpose

The existing harness (`rag/eval/run.py`) reports binary hit@k over 5 gold cases.
That is enough to notice a catastrophic retrieval break and nothing else. Three
later sessions need more:

- **Session 3 (BGE prefix)** needs before/after comparison on identical cases,
  because the change shifts every score.
- **Session 4 (relevance floor)** needs a score distribution to pick a cutoff from.
- **Session 1 (corpus hygiene)** needs to prove that removing duplicate pages
  improved top-k occupancy rather than dropping real content.

Session 0's real job is narrower than "measure retrieval quality." It is to
produce **two score distributions and show whether they separate**: the scores of
correct chunks on answerable questions, versus the top-1 scores on questions the
corpus cannot answer. If those distributions overlap completely, Session 4's
distance floor is not implementable as conceived — and this session is where that
gets discovered, cheaply, rather than after the floor is built.

## Scope constraint

**Session 0 makes zero edits to the retrieval path.** No changes to `retrieve.py`,
`store.py`, `embeddings.py`, or `query_rewrite.py`. A baseline produced by code
that also changed retrieval is not a baseline. All work lands in
`src/stratpoint_rag/rag/eval/` plus its tests.

## Approach

Extend `run.py` in place and add a committed baseline file. Rejected alternatives:

- **Splitting into `gold.py` / `metrics.py` / `report.py`.** Premature. One data
  file, one caller. The split earns its keep when a second consumer exists.
- **Adopting an eval framework (ragas or similar).** Wrong tool. Every question
  in items #1–#4 is a *ranking* question answerable from cosine scores and slugs.
  An LLM judge would add a NIM round-trip per case, nondeterminism, and cost to
  measure something arithmetic — and still could not answer "where do I put the
  cutoff."

Keeping the eval a single readable file matters because its own correctness is
load-bearing for three later sessions.

## Gold set

### Schema

`gold.jsonl` gains four fields:

```json
{"id": "svc-outsystems-01", "q": "Does Stratpoint offer OutSystems development services?",
 "slug": "outsystems-offerings", "expect": "retrieve", "axis": "entity-named"}
{"id": "svc-outsystems-01p", "q": "Do you do OutSystems development?",
 "slug": "outsystems-offerings", "expect": "retrieve", "axis": "pronoun",
 "paraphrase_of": "svc-outsystems-01"}
{"id": "abs-headcount", "q": "How many employees does Stratpoint have?",
 "expect": "abstain"}
```

| Field | Required | Meaning |
|-------|----------|---------|
| `id` | always | Stable case identifier. Baseline diffs key on it, so cases can be reordered or inserted without invalidating history |
| `q` | always | The question |
| `expect` | always | `retrieve` or `abstain` |
| `slug` | when `expect: retrieve` | The page that answers the question. Absent on abstain cases |
| `axis` | when `expect: retrieve` | Phrasing tag for per-axis reporting: `entity-named` or `pronoun` |
| `paraphrase_of` | optional | The `id` this case is a rephrasing of |

The existing 5 rows are migrated to this schema; they become `entity-named`
`retrieve` cases (the AWS Lambda case names no entity but is still not
pronoun-phrased, so it is tagged `entity-named` — the axis distinguishes
anaphoric phrasing, not the literal presence of the company name).

### Authoring method — corpus-first, retriever-blind

1. Stratified sample of pages from `data/index.jsonl`.
2. Read the page; author a question it genuinely answers.
3. Label `slug` as that page.
4. **Only then** run retrieval to observe the score.

Running retrieval first and writing questions around what comes back would encode
current behaviour as ground truth, producing a harness that cannot fail. Some
corpus-first cases will turn out unfairly hard (thin page, fact carried in an
image); those get triaged after the first run — either kept as known-hard with a
note, or replaced. They are not silently deleted for scoring better.

**Sampling exclusion — required for Session 1 to run in parallel.** Do not label
gold cases against pages Session 1 will remove: the three `test-`-prefixed pages,
and the `-pdf` half of any twin pair (17 confirmed). When a `-pdf` twin is
sampled, label the base page instead. Without this the two sessions collide —
Session 1 deletes a page, and Session 0's load-time slug check hard-fails on a
gold set that was correct when authored. The check is the safety net, not the
plan.

### Composition — ~41 rows

- **25 answerable base cases**, stratified ~10 blog / ~8 service-and-offering /
  ~7 company-project-webinar.
- **~8 pronoun paraphrase twins** of those base cases, sharing the gold slug.
- **8 abstention cases.**

Stratification deliberately over-samples non-blog pages. The corpus is ~191 blog
posts against ~150 flat marketing/service/webinar/whitepaper pages, 23
`b_a_goals__*` and 4 `project__*`; proportional sampling would spend half the
budget on blog posts and barely cover the pages carrying real visitor questions.

### Why paraphrase pairs

A pronoun twin sharing a gold slug with its entity-named sibling turns an
anchoring regression into **pair divergence** — twin misses, sibling hits — which
is legible in the report. An aggregate hit@k dip is not: it says something broke,
not what. These pairs are the specific regression guard for the `anchor_entity`
fix in commit `a7cb482`.

### What the abstention cases are

On-topic questions a real visitor would plausibly ask that the corpus genuinely
cannot answer: headcount, revenue, pricing, who the CFO is, offices in countries
Stratpoint is not in.

This definition is forced by the router. `disambiguation/router.py:65-84` already
sets `should_retrieve=False` for harmful, off-topic and greeting inputs, so those
never reach `retrieve()` — a "what's the weather" case would test the router and
pass regardless of whether a relevance floor exists. Only questions that *pass*
the router and *reach* retrieval exercise the floor.

**Stated gap:** near-miss cases are excluded — questions where the corpus holds a
tempting adjacent page that scores highly but does not contain the fact (e.g.
"what AWS certifications does Stratpoint hold?" against an AWS partner page
listing none). These are real and will not be caught by a distance floor, which
is precisely why they are out of scope here: they need a different mechanism than
Session 4 provides. Recording the gap so it is not mistaken for coverage.

## Metrics

**Per answerable case:** rank of the gold slug in top-k (or absent), score of the
gold chunk, score of the top-1 chunk.

**Per abstention case:** score of the top-1 chunk. This is the number that
calibrates Session 4.

**Aggregate:** hit@k overall and per axis; MRR over answerable cases.

**The separation report** — the headline output:

- Distribution (min / p25 / median) of gold-chunk scores on hits.
- Distribution (median / p75 / max) of top-1 scores on abstention cases.
- **Count of abstention cases whose top-1 score exceeds the median gold-chunk
  score.**

That last count is the go/no-go verdict for Session 4. A high count means the
distributions overlap and a single global distance cutoff cannot separate
answerable from unanswerable without unacceptable false abstention. Session 4 is
then either redesigned or dropped — decided on evidence, before implementation.

## Baseline

`rag/eval/baseline.json`, committed to git, keyed by case `id`, storing per case:
rank, gold-chunk score, top-1 score.

- `--baseline` runs the eval and prints per-case rank and score deltas against the
  committed file.
- `--write-baseline` overwrites it, as an explicit and reviewable commit.

The file header records `embed_model` and a corpus fingerprint (page count plus a
hash over the manifest). **Sessions 1 and 3 both invalidate the baseline** —
corpus hygiene changes which chunks exist; the BGE prefix shifts every score. On
fingerprint or model mismatch the run warns loudly instead of silently diffing
across incompatible indexes. Without this, the roadmap's "#3 before #1, never
together" rule is advisory rather than enforceable.

## Run mode and testing

The CLI stays `python -m stratpoint_rag.rag.eval.run`.

One pytest wrapper marked `integration`, matching the repo's existing
`addopts = "-m 'not integration'"` convention. The eval needs a populated
`chroma_db/`, which is gitignored and regenerated from `data/`, so it must not run
in the default unit suite.

Metrics computation and gold-file parsing get **real unit tests against fake
retrieve results** — no index required. That is where the logic lives, and the
harness must be trustworthy before three later sessions lean on it. Coverage:
rank extraction when the gold slug is present, absent, and duplicated across
chunks; MRR arithmetic; per-axis grouping; separation-report statistics; baseline
diffing including the mismatch warning.

## Error handling

| Condition | Behaviour |
|-----------|-----------|
| Gold file names a slug absent from the corpus | Hard fail at load, listing every offender. Catches typos and pages removed by Session 1 |
| Malformed or empty gold row | Fail fast, naming the line number |
| `expect: retrieve` with no `slug`, or `expect: abstain` with one | Hard fail at load |
| Chroma store empty or missing | Clear message pointing at `stratpoint-rag-ingest`, not a stack trace |
| Baseline fingerprint mismatch | Loud warning; the run still completes and reports, but the diff is marked untrustworthy |

## Out of scope

- Answer-quality or faithfulness measurement (the LLM-judge approach, rejected above).
- Near-miss abstention cases (gap stated above).
- Any edit to the production retrieval path.
- Acting on what the eval reveals — fixes are Sessions 1–4.
