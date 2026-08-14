# VLM testing corpus

Two files, the same 10-page RFP twice:

| File | Text layer | Pages routed to vision |
|---|---|---|
| `[TECH] rfp16.pdf` | full | 4 of 10 |
| `[TECH] rfp16_IMAGE.pdf` | none (fully rasterized) | 10 of 10 |

That pairing is the point. The scanned file's transcription can be scored
page-by-page against the digital file's own embedded text layer, so "did the
model read this page" is a number rather than an impression.

## Reproducing a score

Metric: content-word recall, using `transcribe._content_words` — the same
helper `_novelty` uses, so a score here means what it means in the pipeline.

**Note, 2026-08-09:** `_content_words` now counts numbers, which it did not when
the 1.000 figures below were measured. Recall scored with it is therefore
stricter than it was — a page whose years or amounts the model misreads can now
score under 1.000 where it previously could not. Re-baseline before comparing a
new run against the tables below.

1. Read ground truth: `render.open_document("[TECH] rfp16.pdf")`, then
   `doc.page_text(i)` for each page.
2. Run `transcribe_document("[TECH] rfp16_IMAGE.pdf")` with a real
   `NimVisionClient`.
3. Split the result on `^## Page (\d+)$`, strip the `<!-- ... -->` provenance
   comment from each body.
4. Per page: `len(truth_words & body_words) / len(truth_words)`.

Run it at least three times. Table formatting and heading adherence vary
run-to-run on the same input; a single run will mislead you about both.

## Results, 2026-08-09

Full measurements and the reasoning built on them:
`docs/superpowers/specs/2026-08-09-nemotron-vlm-switch-design.md`.

Headline: `nvidia/nemotron-nano-12b-v2-vl` scores 1.000 recall on all ten
scanned pages at 3,755 prompt tokens per page, against meta's 6,431. Raising
the raster to 1536x1988 and batching multiple pages per call were both probed
and both rejected.

## Post-switch validation, 2026-08-09

Run against the shipped implementation (nemotron default, no frequency penalty,
`VISION_TIMEOUT=45`, `_clamp_headings` live), one run per file:

| File | `pages_failed` | vision calls | total tokens | recall, pages 1-10 | shallow headings |
|---|---|---|---|---|---|
| `[TECH] rfp16.pdf` | `[]` | 4 | 23,202 | 1.000 on every page | none |
| `[TECH] rfp16_IMAGE.pdf` | `[]` | 10 | 40,124 | 1.000 on every page | none |

No drift from the numbers above. The empty `shallow_headings` column is the
clamp doing its job — the raw model output in three of six pre-switch runs
carried `#`/`##` headings, and none reach the artifact now.

### `pages_failed` is not reliably `[]` — budget for ~3%

Across six further rate-limited runs of the scanned file (60 vision pages),
**two pages failed, both with `The read operation timed out`** — one in a
control run, one in an ablated run. That is `VISION_TIMEOUT` firing on the
endpoint's throttle-by-*delaying* behaviour, working exactly as designed: an
indefinite stall becomes one recorded entry in `pages_failed` instead of a
hung parse.

So a single clean run is luck, not the contract. If you are re-validating, read
the failure *reason* out of the provenance comment rather than counting
failures — a read timeout is the endpoint, and a refusal or short response
would be the model. Do not retune `VISION_TIMEOUT` off a couple of timeouts;
that soft failure is the degradation the page loop is built around.

## Prompt ablation, 2026-08-09

Question: are `TRANSCRIPTION_PROMPT`'s three anti-summarization bullets
("Transcribe EVERY word...", "Do not add commentary...", "Do not interpret...")
doing anything on nemotron, or are they meta-era cargo?

Method: three control runs interleaved with three ablated runs over
`[TECH] rfp16_IMAGE.pdf`, spaced to stay under the 40 RPM limit. Two metrics,
because recall alone only tests the first bullet — summarization *drops* words,
while commentary and interpretation *add* them and are invisible to recall.

| Arm | Runs | Recall (pages that returned) | Commentary phrases | Output chars |
|---|---|---|---|---|
| control | 3 | 1.000 on every page | 0 | ~11,400 |
| ablated | 3 | 1.000 on every page | 0 | ~11,980 |

**No measurable effect on this corpus.** The bullets are kept anyway — the
reasoning, and the trap in the original meta measurement, are recorded in
`docparse/prompts.py` constraint 2. Read that before deleting them.

A first, unspaced pass fired ~54 calls in a few minutes and showed 2 failed
pages in the ablated arm against 0 in control. That was rate limiting landing
unevenly, not a prompt effect; it disappeared once the runs were spaced. Space
your runs, or you will measure the endpoint instead of the model.

