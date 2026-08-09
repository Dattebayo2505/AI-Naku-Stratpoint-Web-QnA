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

