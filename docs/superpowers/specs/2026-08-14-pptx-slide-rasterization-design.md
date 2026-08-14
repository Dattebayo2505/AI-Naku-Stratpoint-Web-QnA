# PPTX slide rasterization for the vision model

**Date:** 2026-08-14
**Component:** `stratpoint_rag.docparse`
**Status:** design approved, ready for implementation planning

## Problem

`docparse` accepts PDFs and images. A `.pptx` is rejected at the boundary, and
the rejection is deliberate — `render.sniff_kind`'s docstring records the
reasoning:

> `.pptx`/`.docx` are deliberately unsupported: PyMuPDF cannot open them,
> LibreOffice headless is a ~400 MB root install on a 6 GB LXC, and python-pptx
> is text-only — it misses every diagram and architecture slide, which is
> exactly where requirements live. "Export your deck to PDF" is a five-second
> ask.

Clients send decks. Asking them to re-export is friction at the exact moment we
are trying to remove it, and the person uploading a brief is often not the
person who can re-export it.

**This design reverses that decision.** The reversal is deliberate and scoped:
LibreOffice becomes a documented hard dependency, and the python-pptx objection
is not answered — it is *sidestepped*, because we never extract text from the
deck at all. Every slide becomes an image.

## Decisions taken

| Question | Decision |
|---|---|
| LibreOffice missing | Hard dependency. Documented as required in README, CLAUDE.md and the deploy doc. No graceful capability-detection fallback. |
| Conversion timing | At upload. The derived PDF is cached beside the original in the upload dir. |
| Converted PDF's text layer | Used as the figure pass's novelty baseline **only**. Never emitted into the artifact. |
| Format scope | `.pptx` (Office Open XML) only. Legacy binary `.ppt` stays rejected. |
| Page cap | Reuse `DOCPARSE_MAX_PAGES` (40). No slides-specific knob. |

## Non-goals

- **No text extraction.** Not python-pptx, not the converted PDF's text layer,
  not a hybrid. One image per slide, transcribed by the vision model. This is
  the point of the feature, not an implementation shortcut.
- **No `.docx`/`.xlsx`/`.odp`.** LibreOffice converts them and the machinery
  would be identical, but they are not decks and the diagram argument that
  justifies the vision route does not apply to a Word document, which has a
  perfectly good text layer.
- **No legacy `.ppt`.** Its OLE2 header is shared with `.doc` and `.xls`, so
  accepting it means either extension-trusting at a public HTTP endpoint or
  parsing a compound-file format to tell them apart. Not worth it for a format
  that is now rare.

## Architecture

A new module, `docparse/slides.py`, owns every LibreOffice call — the same
containment argument `render.py` makes for PyMuPDF and `pdf_gen/pdf_service.py`
makes for Playwright. It is not folded into `render.py`: that module's docstring
promises a contained swap to `pypdfium2`, and process spawning, binary
discovery, timeouts and temp-profile management do not belong behind that
promise.

The converter sits behind a `Converter` protocol, the testability seam
`stratpoint_crawl` already proves with `Fetcher`. Unit tests inject a fake that
writes a stub PDF; production uses the real `subprocess` runner. This is what
lets the whole deck path be tested on a machine with no LibreOffice.

### Data flow

```
POST /upload
  store.save_upload(deck.pptx)
  _page_count(record.path)
      slides.ensure_pdf(path) ──> soffice --headless --convert-to pdf
      │                           writes <upload_dir>/<upload_id>/converted.pdf
      └─ render.open_document(converted.pdf, slides=True) ──> slide count

POST /upload/{id}/parse
  transcribe_document(record.path)
      sha256 + source_file   <- read from the ORIGINAL .pptx
      slides.ensure_pdf(path) ──> cache hit, no subprocess
      render.open_document(converted.pdf, slides=True) ──> kind == "slides"
      _needs_vision() ──> True for every page, unconditionally
      rasterize(i) + page_text(i) ──> vision worker
                                      (text layer = novelty baseline only)
```

`ensure_pdf` is **idempotent** and returns its input unchanged for a
non-deck, so both entry points call it unconditionally and the second call
costs a `stat` rather than a ten-second conversion. The cache path is
`path.parent / "converted.pdf"` — a fixed name inside the upload's own
directory, so it is scoped by `upload_id`, swept by the existing TTL and purge
machinery in `store.py` with no change, and never collides across uploads.
`store.py`'s layout docstring gains the file.

### Provenance is split, on purpose

`transcribe_document` hashes and names the **original** `.pptx` while opening
the **derived** PDF. The artifact's `source_file` and `sha256` must describe the
file the user uploaded; a hash of a PDF they never saw cannot be checked against
anything they hold, and the sha256 upload cache in `store.find_by_sha256`
already keys on the original bytes.

### `Document.kind` gains `"slides"`

`open_document(path, *, slides: bool = False)` stamps `kind="slides"` instead of
`"pdf"`. Two consequences, both intended:

- `rasterize` branches on `if self.kind == "image"` to clamp zoom to native
  pixel density. `"slides"` falls through to the vector path, which is correct —
  LibreOffice emits vector PDF, so scaling up is genuine detail at identical
  token cost.
- 16:9 slides are landscape, so they take `max_h = MAX_WIDTH` and render about
  1120x630. No new raster constants; the tile budget is unchanged.

### Forcing the vision route

`_needs_vision` gains one clause at the top:

```python
if doc.kind in ("image", "slides"):
    return True
```

The converted PDF carries a perfect text layer — it is the real slide text, not
OCR — so without this clause almost every slide takes the free text route and
the feature does nothing. The text layer is still read and handed to the worker
as the figure pass's novelty baseline, exactly as the `has_text` path in
`_render_page` already expects. That is the best ground truth available on a
deck, it costs no extra call, and it is what lets the figure pass distinguish
"the model described the architecture diagram" from "the model retyped the
bullet points".

## `slides.py` in detail

### Detection

`is_pptx(path) -> bool` opens the file as a zip and checks for a
`ppt/presentation.xml` member. Content-based, so a `.docx` renamed `.pptx` is
rejected and the project's "content decides, never the extension" rule survives
intact.

`sniff_kind` gains a `"zip"` return for the `PK\x03\x04` magic. It keeps its
pure-bytes-in contract — it sees 16 bytes and cannot open a zip — so
`open_document` resolves `"zip"` to a deck or to `UnsupportedDocument`.

### Four failure modes that must be handled

1. **A private user profile per invocation is mandatory.** Pass
   `-env:UserInstallation=file:///<tmpdir>`. Without it, an already-running
   soffice — or a second concurrent upload — makes the invocation **exit 0
   having converted nothing**. This is the classic headless failure and it looks
   exactly like success.
2. **The exit code is not the check.** Verify the output PDF exists and is
   non-empty. soffice returns 0 on inputs it silently declined.
3. **A timeout.** `subprocess.run(timeout=SOFFICE_TIMEOUT)`, default 120s, child
   killed on expiry. Same reasoning as `VISION_TIMEOUT`: convert an indefinite
   stall into one clean recorded error.
4. **Binary discovery.** `SOFFICE_BINARY` env override first, then
   `shutil.which("soffice")`, then `which("libreoffice")`, then the Windows
   default `C:\Program Files\LibreOffice\program\soffice.exe`.

### Concurrency

Conversion runs on the request thread inside `/upload`. It is one subprocess per
upload and uploads are already serialized by the user's own pace; no pool, no
semaphore. The per-invocation profile dir is what makes two simultaneous
uploads safe.

## UI

`ui/app.py`'s `ACCEPTED_TYPES` gains `"pptx"`, and the uploader label changes
from "Drop a PDF or image" to include decks. `st.file_uploader`'s `type=` stays
a client-side convenience — `/upload` is reachable without Streamlit, which is
why detection is content-based at the API.

`UploadResponse.pages` carries the slide count for a deck. The confirmation
dialog's wording is left as-is: "pages" reads acceptably for slides, and a
format-dependent noun is churn for no gain.

## Error handling

`ConversionFailed(UnsupportedDocument)` — subclassing is load-bearing.
`app.py`'s two existing `except (UnsupportedDocument, EncryptedDocument)`
handlers already map it to HTTP 400 and already call `delete_upload` so nothing
rejected is retained. No new handler wiring at the API layer.

A missing LibreOffice binary raises `RuntimeError`, matching how a missing API
key is signalled today. `parse_upload` already catches `RuntimeError` and
returns 503. `/upload` does **not** — its handler catches only
`(UnsupportedDocument, EncryptedDocument)` around `_page_count`, so a missing
binary would currently escape as an unhandled 500. Implementation must add a
`RuntimeError -> 503` handler to `upload_file`, deleting the stored upload the
same way the 400 path does. 503 is the right code: the file is fine, the server
is misconfigured.

The rejection message for an unsupported file becomes "... is not a PDF,
PowerPoint deck (.pptx), or supported image (png, jpg, jpeg, webp, tiff)."

## Cost

Every slide is a vision call. A 40-slide deck is 40 calls plus up to 12
figure-pass calls, against NIM's 40 requests/minute. Today that worst case only
arises on a fully-scanned PDF, which is rare; with decks it becomes the normal
case, every time.

`DOCPARSE_MAX_PAGES` (40) is reused rather than adding a slides-specific cap:
the ceiling and its reasoning are already documented in `config.max_pages`, and
a second knob nobody tunes is worse than one well-explained one. The honest
statement — which belongs in the docstring — is that decks make the documented
worst case routine rather than exceptional.

## Configuration

Two new variables, mirrored blank into `.envexample` per the repo rule:

- `SOFFICE_BINARY` — explicit path override. Blank = auto-discovery.
- `SOFFICE_TIMEOUT` — seconds, default 120.

## Testing

Unit tests run with **no LibreOffice installed**, via the `Converter` seam:

- `ensure_pdf` caches (second call spawns nothing), is a no-op for a PDF path,
  and raises `ConversionFailed` when the converter produces no output or an
  empty file.
- Command construction includes `--headless`, `--convert-to pdf`, and the
  `-env:UserInstallation` flag. That last assertion is the regression guard for
  failure mode 1, which is silent in production.
- `is_pptx` is true for a minimal zip containing `ppt/presentation.xml`, false
  for a `.docx`-shaped zip and for a malformed one.
- `_needs_vision` returns `True` for a `kind="slides"` document carrying a text
  layer well over `text_layer_min_chars`. This is the regression guard for "no
  text extracts".
- `transcribe_document` over a fake-converted deck stamps `source_file` and
  `sha256` from the original `.pptx`, not the derived PDF.

Two existing tests invert rather than being deleted:

- `tests/test_docparse_render.py::test_pptx_is_rejected_despite_its_extension`
  becomes `test_a_docx_renamed_pptx_is_still_rejected`.
- `tests/test_docparse_api.py::test_upload_rejects_a_disguised_pptx` becomes
  `test_upload_rejects_a_malformed_zip`. Its existing assertion that `"pdf"`
  appears in the detail still holds against the new message.

One `-m integration` test converts a real `.pptx` with the real binary,
deselected by default like the live crawl test.

## Documentation to update

- `render.sniff_kind` docstring — the paragraph quoted at the top of this spec.
- `CLAUDE.md` — the docparse section: the deck route, the always-vision rule,
  and LibreOffice as a hard dependency.
- `README.md` — install instructions on all three toolchain paths (uv, pip+venv,
  conda), since LibreOffice is a system package none of them install.
- `docs/deploy-lxc-6gb-no-docker.md` — the ~400 MB install on the 6 GB target,
  which is the constraint the original decision was made against.
- `.envexample` — `SOFFICE_BINARY`, `SOFFICE_TIMEOUT`.

## Known risk, accepted

LibreOffice parses an attacker-controllable file. It is a large C++ codebase
with a history of parser CVEs, and it now runs on every deck upload. Bounded by
`upload_max_bytes` (25 MB) and the conversion timeout, and it runs as the
non-root API user — but it is a materially larger attack surface than PyMuPDF
alone, and it is worth recording next to the existing accepted risk of prompt
injection via uploaded content.
