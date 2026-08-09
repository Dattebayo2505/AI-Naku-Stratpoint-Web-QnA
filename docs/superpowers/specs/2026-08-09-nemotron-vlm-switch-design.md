# Design — switch docparse hop 1 to `nvidia/nemotron-nano-12b-v2-vl`

**Date:** 2026-08-09
**Scope:** `stratpoint_rag.docparse` hop 1 (vision transcription) only. Hop 2
runs on `LLM_MODEL` and is not touched.

## Problem

Every tuning constant in the hop-1 vision path was measured against
`meta/llama-3.2-11b-vision-instruct`: the frequency penalty, the repetition
backstop, the 1120px raster cap, the 90s timeout, and the figure pass. Swapping
the model invalidates each of those justifications independently. The question
is not "does the new model work" but "which of these mechanisms still earns its
place, and what new failure modes replace the old ones".

## Evidence

All numbers below come from a live probe run on 2026-08-09, not from the model
card. The probe drove the real `transcribe_document`, so routing, assembly, the
figure pass and usage accounting were all exercised as production would.

### Setup

Two files in `docs/vlm-testing/`, the same 10-page RFP twice:

| File | Pages routed to vision |
|---|---|
| `[TECH] rfp16.pdf` (digital, full text layer) | 4 of 10 |
| `[TECH] rfp16_IMAGE.pdf` (fully rasterized) | 10 of 10 |

This pair is the reason the probe can be scored objectively: the scanned file's
transcription is compared page-by-page against the **digital file's own embedded
text layer**. The metric is content-word recall, using the same `_content_words`
helper `_novelty` already uses.

Runs: one baseline over **both** files at 1120×1449 with
`frequency_penalty=0.3`, then, on the scanned file only, 2 more with the penalty,
3 with no penalty, and 1 at 1536×1988 — six scanned runs in total, so the
run-to-run variance of a stochastic model is visible rather than assumed.

### Results

- **Fidelity: 1.000 content-word recall on all 10 scanned pages**, under both
  penalty settings. Zero failed pages, zero refusals, zero empty replies.
- **Cost: a flat 3,755 prompt tokens per page** at 1120×1449 (3,602 for a
  figure-pass call). Meta billed 6,431 — 1,601 per tile × 4 tiles + 27. That is
  a 42% reduction per page.
- **Latency:** 3.5–19s per call; 28.8–44.9s wall for the fully-scanned 10-page
  file at concurrency 4.
- **No degeneration loops.** `finish_reason` was `"stop"` on every call in every
  run. Completions ran 42–448 tokens against the 2,048 ceiling.
- **Reasoning is off by default, as documented.** `reasoning_content` is present
  as a message key in every response and is always empty; no `<think>` tags ever
  appeared in content. `/no_think` is unnecessary.

### What this says about each existing mechanism

| Mechanism | Finding | Decision |
|---|---|---|
| `FREQUENCY_PENALTY = 0.3` | No measurable effect. Well-formed table separator rows across 3+3 runs: with penalty 0/1/3, without 0/1/3 — the same distribution. The degeneration loop it was tuned against never occurred. | **Remove** |
| `_collapse_repetition` / `_MAX_LINE_REPEATS` | Never fired in any run. | **Remove** |
| `MAX_WIDTH = 1120` | 1536×1988 billed **identical** prompt tokens, ran 3.3× slower (111.7s vs 33.7s), and lost page 10 to the timeout. Recall fell 1.000 → 0.900 as a result. | **Keep, rejustify** |
| `VISION_TIMEOUT = 90` | Never hit at 1120px; did fire at 1536px. It guards endpoint throttle-by-delaying, which is not a model property. | **Keep, retune to 45** |
| Figure pass + novelty gate | Still earns its place. Fired on digital pages 1, 3, 5. Page 3 recovered map-internal labels the transcription pass missed entirely: "South Alamo Street", "E. Cesar Chavez Blvd", "US-281", "Yanaguana Garden", "Civic Park", "Tower Park", "Currently dedicated parkland - 19 acres". Nemotron shares meta's text-transcriber posture. | **Keep unchanged** |
| One image per request | The HTTP 400 (`"At most 1 image(s)"`) was **meta's** behavior. Nemotron's card claims 5. Untested. | **Keep, downgrade the claim** |

### New failure modes

Two behaviors meta did not have:

1. **Heading-level violations.** Nemotron emits `#` and `##` headings despite
   the prompt rule, in 3 of 6 scanned runs and in the digital run. Observed:
   `# General Information`, `# Major Tree Varieties in new Civic Park` (×3),
   `## Background`, `## Deliverables` (×3), `## Site Information`. A `##`
   heading in a page body collides with the Python-owned `## Page N` wrapper.
2. **One fabricated table**, in 1 of 6 runs: a "Characteristic / Number of
   people" demographic table with rows 18-24 through 75+, on page 5 — a page
   carrying two aerial maps and no table at all.

A third, minor: the figure pass sometimes returns a verbatim duplicate of the
caption lines the transcription pass already produced (digital page 5). Noise,
not data loss.

## Design

### Config — `docparse/config.py`

- `vision_model()` default becomes `"nvidia/nemotron-nano-12b-v2-vl"`.
- Delete `FREQUENCY_PENALTY` and its `__all__` entry.
- `VISION_TIMEOUT` 90 → **45**. Rationale in the comment: pages return in
  3.5–19s, so 45s is ~2.4× the slowest observed page and does not clip a merely
  slow one; the ceiling still exists because the endpoint throttles by delaying
  rather than returning 429, which is endpoint behavior and not a model
  property. Worst case for a wholly-stalled 40-page scan falls from 900s to
  450s.
- `MAX_TOKENS = 2048` and `TEMPERATURE = 0.1` unchanged. The `MAX_TOKENS`
  comment is rewritten: the ceiling is now pure insurance, since the longest
  observed completion was 448 tokens.
- `figure_pass_novelty()` and `figure_pass_min_text_chars()` unchanged.

### Client — `docparse/nim.py`

- Remove `frequency_penalty` from the vision request body.
- The module docstring keeps its central claim — the OpenAI multimodal payload
  form is mandatory and the HTML-`<img>` form is a silent trap — because that is
  a property of the endpoint, not the model. Meta's token-accounting anecdote is
  replaced by nemotron's flat 3,755 prompt tokens per page.
- The "one image per request" note is downgraded from a measured constraint to a
  design choice: we send one image because Python owns the `## Page N` wrapper
  and per-page numbering drives `pages_failed`. Nemotron nominally supports up
  to 5; that path is untested and unadopted. The same correction applies to the
  `VisionClient` Protocol docstring in `clients.py`.

### Transcription — `docparse/transcribe.py`

Removed:

- `_collapse_repetition` and `_MAX_LINE_REPEATS`.
- Their three call sites (two in `_render_page`, one in `_figure_pass`).
- The second `_is_unusable` re-check in `_render_page`, which existed only
  because collapsing could empty a reply.
- The `note` plumbing simplifies accordingly — `note` now originates only from
  the figure pass.

Added — `_clamp_headings(text) -> str`:

- Demotes line-initial `#` and `##` to `###` in a page body. Applied to the
  transcription reply and to the figure-pass block.
- Its comment must state explicitly that this is **within-page** normalization
  enforcing the wrapper's ownership of `##`, and is *not* the cross-page
  heading-level post-processing that `prompts.py` forbids. That rule bans
  inferring document structure from N independent page guesses; this only
  prevents a page body from claiming the wrapper's level.
- The prompt rule stays as the nudge. This follows the precedent the removed
  `FREQUENCY_PENALTY` set: a sampling-level or prompt-level instruction is a
  nudge, and where the cost of it not landing is a corrupted artifact, the
  backstop is deterministic.

Unchanged: `_is_unusable`, `_REFUSAL`, `_novelty`, `_figure_pass`,
`_needs_vision`, `_assemble`, `_frontmatter`, and the three threading rules in
the module docstring.

### Rendering — `docparse/render.py`

`MAX_WIDTH = 1120` and `MAX_HEIGHT_PORTRAIT = 1456` are unchanged; the comment
is rewritten. The old justification was billing — 1,601 tokens per tile, a
4-tile hard cap, so pixels past ~1120px were discarded and paid for. That is
meta's billing model and does not describe nemotron, which bills the same at
1536×1988 as at 1120×1449. **The cap now stands on latency:** the larger raster
was 3.3× slower and cost a page to the timeout, for no measured quality gain.

### Environment and docs

- `.envexample`: delete `VISION_META_MODEL` and `NVIDIA_VISION_META_API_KEY`;
  update the `VISION_MODEL` comment to name the new default.
- `CLAUDE.md`: the docparse "Key design decisions" section states meta's numbers
  in four places — the 1120px tile cap and its billing rationale, one image per
  request, the 90s `VISION_TIMEOUT`, and the degeneration-loop mitigation. All
  four are updated; the removed mechanisms are struck.
- `docs/vlm-testing/`: the probe harness and a findings note are committed, so
  these measurements are recorded rather than re-derived. This follows the
  repo's existing habit of writing measurements into the artifact that depends
  on them.

## Testing

Offline unit tests, using the existing injected `VisionClient` fake — no network:

- `tests/test_docparse_nim_client.py`: replace the `frequency_penalty` assertion
  with its inverse — the key must be absent from the vision body.
- `tests/test_docparse_transcribe.py`: delete the `_collapse_repetition` tests.
  Add `_clamp_headings` tests covering `#` → `###`, `##` → `###`, `###` and
  deeper untouched, and a mid-line `#` untouched. Add one end-to-end assertion
  that a model reply containing `## Deliverables` appears as `### Deliverables`
  in the assembled artifact, with the only `##` lines being page wrappers.

Live re-validation, following the crawler's "validating extraction changes"
precedent: re-run both PDFs through the probe after the change and confirm
recall stays at 1.000 with no heading violations surviving into the artifact.

## Known residual, not addressed here

The fabricated table (1 run in 6) is a hallucination on a figure-heavy page.
Nothing in this change mitigates it, and the schema cannot catch it — hop 1's
output is free-form Markdown by design. It is recorded as known behavior
alongside the existing deferred-by-decision entries in `docparse/__init__.py`.

Multi-image batching (nemotron's card claims 5 images per request) is out of
scope. It is untested, and it conflicts with Python owning the page wrapper.
