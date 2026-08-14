# PPTX Slide Rasterization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Accept `.pptx` uploads by converting them to PDF with headless LibreOffice, then transcribing every slide as an image through the vision model — no text extraction anywhere in the path.

**Architecture:** A new `docparse/slides.py` owns every LibreOffice call, mirroring the containment `render.py` gives PyMuPDF and `pdf_gen/pdf_service.py` gives Playwright. Conversion runs once at `/upload` and caches `converted.pdf` inside the upload's own directory; `slides.open_brief()` becomes the single entry point that both `/upload`'s page count and `transcribe_document` use. The derived `Document` carries `kind="slides"`, which forces `_needs_vision` to `True` for every page.

**Tech Stack:** Python 3.13, uv, PyMuPDF, FastAPI, Streamlit, pytest. New system dependency: LibreOffice (headless). New dev-only Python dependency: `python-pptx` (test fixture generation only).

**Spec:** `docs/superpowers/specs/2026-08-14-pptx-slide-rasterization-design.md`

## Global Constraints

- **No text extraction from decks, anywhere in the output.** Every slide is transcribed from its rasterized image. The converted PDF's text layer is read *only* to serve as the figure pass's novelty baseline. This is the point of the feature, not an implementation detail.
- **Scope is `.pptx` (Office Open XML) only.** Legacy binary `.ppt`, `.docx`, `.xlsx` and `.odp` stay rejected.
- **Content decides, never the extension.** A `.docx` renamed `.pptx` must be rejected. Detection reads inside the zip.
- **Every LibreOffice call lives in `slides.py`.** Do not add `subprocess` usage to `render.py` — its docstring promises a contained swap to `pypdfium2`.
- **Unit tests must pass on a machine with no LibreOffice installed.** Only the single `@pytest.mark.integration` test may invoke the real binary.
- **`ensure_pdf` is idempotent** and returns its input unchanged for non-decks, so both call sites invoke it unconditionally.
- **Provenance describes the original.** `source_file` and `sha256` come from the uploaded `.pptx`, never the derived PDF.
- **Reuse `DOCPARSE_MAX_PAGES` (40).** No slides-specific page cap.
- Run tests with `uv run pytest`. Commit after every task.

---

### Task 1: Deck detection — `slides.py` skeleton

**Files:**
- Create: `src/stratpoint_rag/docparse/slides.py`
- Create: `tests/test_docparse_slides.py`

**Interfaces:**
- Consumes: `render.UnsupportedDocument` (existing, `render.py:84`)
- Produces:
  - `slides.ConversionFailed(render.UnsupportedDocument)` — exception
  - `slides.is_pptx(path: str | Path) -> bool`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_docparse_slides.py`:

```python
"""docparse.slides — LibreOffice conversion of .pptx decks into PDF.

Fixtures are *generated* zips rather than committed binaries, matching
tests/test_docparse_render.py: deterministic, tiny, and the construction
documents what "is a deck" actually means.

Nothing in this file requires LibreOffice to be installed. The converter is
injected; only the integration test in Task 6 spawns the real binary.
"""

from __future__ import annotations

import zipfile

import pytest

from stratpoint_rag.docparse import render, slides


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def deck(tmp_path):
    """A minimal zip shaped like a .pptx: it carries ppt/presentation.xml."""
    path = tmp_path / "brief.pptx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("ppt/presentation.xml", "<presentation/>")
        z.writestr("ppt/slides/slide1.xml", "<sld/>")
    return path


@pytest.fixture
def docx(tmp_path):
    """A Word document renamed .pptx. Same magic bytes, not a deck."""
    path = tmp_path / "notadeck.pptx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<document/>")
    return path


# ── detection ───────────────────────────────────────────────────────────────


def test_is_pptx_accepts_a_deck(deck):
    assert slides.is_pptx(deck) is True


def test_is_pptx_rejects_a_renamed_docx(docx):
    """Content decides, not the filename — the extension says otherwise here."""
    assert slides.is_pptx(docx) is False


def test_is_pptx_rejects_a_malformed_zip(tmp_path):
    path = tmp_path / "broken.pptx"
    path.write_bytes(b"PK\x03\x04" + b"\x00" * 64)
    assert slides.is_pptx(path) is False


def test_is_pptx_rejects_a_pdf(tmp_path):
    path = tmp_path / "brief.pdf"
    path.write_bytes(b"%PDF-1.7\n%%EOF\n")
    assert slides.is_pptx(path) is False


def test_is_pptx_rejects_a_missing_file(tmp_path):
    """A path that does not exist is not a deck; it must not raise."""
    assert slides.is_pptx(tmp_path / "gone.pptx") is False


def test_conversion_failed_is_an_unsupported_document():
    """Subclassing is load-bearing: app.py's existing 400 handler catches
    UnsupportedDocument, so ConversionFailed needs no new wiring there."""
    assert issubclass(slides.ConversionFailed, render.UnsupportedDocument)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_docparse_slides.py -v`

Expected: collection error — `ImportError: cannot import name 'slides' from 'stratpoint_rag.docparse'`

- [ ] **Step 3: Write the minimal implementation**

Create `src/stratpoint_rag/docparse/slides.py`:

```python
"""PowerPoint decks: convert to PDF with headless LibreOffice, then rasterize.

Every LibreOffice call in the project lives here, the same containment
``render.py`` gives PyMuPDF and ``pdf_gen/pdf_service.py`` gives Playwright.
Process spawning, binary discovery, timeouts and profile management do not
belong behind ``render.py``'s promise of a contained swap to ``pypdfium2``.

A deck is never text-extracted. It is converted to PDF purely so PyMuPDF can
rasterize one image per slide for the vision model — python-pptx would give us
the words and miss every architecture diagram, which is exactly where the
requirements live.
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

from stratpoint_rag.docparse.render import UnsupportedDocument

log = logging.getLogger(__name__)

__all__ = [
    "ConversionFailed",
    "is_pptx",
]

# The part every Office Open XML presentation carries and no other OOXML
# format does. A .docx has word/document.xml, a .xlsx has xl/workbook.xml.
_PPTX_MARKER = "ppt/presentation.xml"


class ConversionFailed(UnsupportedDocument):
    """LibreOffice could not turn this deck into a PDF.

    Subclasses UnsupportedDocument on purpose: ``api/app.py`` already maps that
    to HTTP 400 and already deletes the stored upload, so a failed conversion
    needs no new handler wiring at the boundary.
    """


def is_pptx(path: str | Path) -> bool:
    """True when the file is an Office Open XML presentation.

    Content-based, because ``.pptx`` and ``.docx`` are byte-identical at the
    magic-number level — both are ``PK\\x03\\x04`` zips — and ``/upload`` is a
    real HTTP endpoint reachable without Streamlit's client-side ``type=``
    filter. Reading one member name inside the zip is what keeps the project's
    "content decides, never the extension" rule intact.

    Never raises: a missing, unreadable or malformed file is simply not a deck,
    and the caller's next step (``render.open_document``) produces the single
    well-worded rejection.
    """
    try:
        with zipfile.ZipFile(path) as z:
            return _PPTX_MARKER in z.namelist()
    except (OSError, zipfile.BadZipFile):
        return False
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_docparse_slides.py -v`

Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/stratpoint_rag/docparse/slides.py tests/test_docparse_slides.py
git commit -m "feat(docparse): content-based .pptx detection"
```

---

### Task 2: `render.py` — the `zip` kind, the `slides` kind, the new message

**Files:**
- Modify: `src/stratpoint_rag/docparse/render.py:92-112` (`sniff_kind`), `:334-364` (`open_document`), `:21-30` (`__all__`)
- Modify: `tests/test_docparse_render.py:130-149`
- Modify: `tests/test_docparse_api.py:78-85`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `render.sniff_kind(head: bytes) -> str | None` now also returns `"zip"` for `PK\x03\x04`
  - `render.open_document(path: str | Path, *, slides: bool = False) -> Document` — stamps `kind="slides"` when `slides=True`, raises `UnsupportedDocument` for `kind == "zip"`

Note for the implementer: `open_document` never converts. It is handed either an
original file or an already-converted PDF. A zip reaching it always means "not a
deck we can use", so rejecting `"zip"` outright is correct and keeps this module
free of any LibreOffice knowledge.

- [ ] **Step 1: Write the failing tests**

In `tests/test_docparse_render.py`, change the `test_sniff_kind_recognises_supported_formats` parametrize list to add a `zip` case, and remove `b"PK\x03\x04"` from the rejects list. Replace lines 130-149 with:

```python
@pytest.mark.parametrize(
    "head",
    [
        b"\xd0\xcf\x11\xe0",  # legacy .doc / .ppt — OLE2, deliberately unsupported
        b"GIF89a",
        b"",
        b"%PD",  # truncated
    ],
)
def test_sniff_kind_rejects_unsupported_formats(head):
    assert render.sniff_kind(head) is None


def test_sniff_kind_reports_a_zip_container():
    """Head-only classification cannot tell a .pptx from a .docx — both are
    zips. open_document resolves it; sniff_kind keeps its pure-bytes contract."""
    assert render.sniff_kind(b"PK\x03\x04") == "zip"


def test_a_zip_is_rejected_by_open_document(tmp_path):
    """Content decides, not the filename. open_document never converts, so any
    zip reaching it is a file we cannot use."""
    path = tmp_path / "deck.pdf"
    path.write_bytes(b"PK\x03\x04" + b"\x00" * 100)
    with pytest.raises(render.UnsupportedDocument) as ex:
        render.open_document(path)
    assert "pptx" in str(ex.value)


def test_slides_flag_stamps_the_slides_kind(text_pdf):
    """A converted deck is a PDF on disk; the flag is how the pipeline knows
    it came from slides and must take the vision route regardless."""
    with render.open_document(text_pdf, slides=True) as doc:
        assert doc.kind == "slides"
```

Leave `test_sniff_kind_recognises_supported_formats`'s parametrize list
unchanged — the `zip` case is covered by the dedicated test above.

In `tests/test_docparse_api.py`, replace `test_upload_rejects_a_disguised_pptx` (lines 78-85) with:

```python
def test_upload_rejects_a_malformed_zip(pdf_bytes):
    """st.file_uploader's type= is a client-side filter; /upload is reachable
    without Streamlit, so content decides. A zip that is not a real deck is
    rejected at the boundary like any other unsupported file."""
    r = _upload(b"PK\x03\x04" + b"\x00" * 128, name="deck.pdf")

    assert r.status_code == 400
    assert "pdf" in r.json()["detail"].lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_docparse_render.py -k "zip or slides_flag" -v`

Expected: `test_sniff_kind_reports_a_zip_container` FAILS (`assert None == 'zip'`) and `test_slides_flag_stamps_the_slides_kind` FAILS (`TypeError: open_document() got an unexpected keyword argument 'slides'`).

- [ ] **Step 3: Implement — `sniff_kind`**

In `render.py`, replace the `sniff_kind` docstring paragraph about pptx and add the zip branch. Replace lines 92-112 with:

```python
def sniff_kind(head: bytes) -> str | None:
    """Classify a file by magic bytes: 'pdf', 'image', 'zip', or None.

    Content decides, never the extension. ``st.file_uploader``'s ``type=`` is a
    client-side filter, and ``/upload`` is a real HTTP endpoint reachable
    without Streamlit.

    ``'zip'`` is deliberately not a verdict. A ``.pptx`` and a ``.docx`` are
    byte-identical here — both are ``PK\\x03\\x04`` — and this function's
    contract is 16 bytes in, so it cannot open the container to tell them
    apart. It reports what it saw and leaves the resolution to
    :func:`open_document`, which has the path.

    Legacy binary ``.ppt``/``.doc``/``.xls`` (OLE2) stay unsupported: one
    header covers all three, so accepting them means either trusting the
    extension at a public endpoint or parsing a compound-file format. Not worth
    it for formats this rare. "Export it to PDF" remains the answer there.
    """
    if head.startswith(_PDF_MAGIC):
        return "pdf"
    if any(head.startswith(m) for m in _IMAGE_MAGICS):
        return "image"
    # WEBP is RIFF-framed: "RIFF" <4-byte size> "WEBP".
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image"
    if head.startswith(_ZIP_MAGIC):
        return "zip"
    return None
```

Add the magic constant beside the others (after `_IMAGE_MAGICS`, around line 81):

```python
_ZIP_MAGIC = b"PK\x03\x04"
```

- [ ] **Step 4: Implement — `open_document`**

Replace `open_document` (lines 334-364) with:

```python
def open_document(path: str | Path, *, slides: bool = False) -> Document:
    """Validate and open a brief.

    ``slides=True`` marks a PDF that was produced by converting a deck, so the
    page loop can force every page down the vision route. The caller sets it;
    nothing about the converted PDF's own bytes distinguishes it from any other
    PDF, and by design it carries a full text layer.

    Never converts anything. A zip arriving here is a file we cannot use —
    ``slides.open_brief`` is the entry point that turns a deck into a PDF
    first — so it is rejected with the same message as any other unsupported
    format.

    Raises :class:`UnsupportedDocument` when the magic bytes are not a PDF or a
    supported image, and :class:`EncryptedDocument` for a password-protected
    PDF.
    """
    path = Path(path)
    try:
        head = path.open("rb").read(16)
    except OSError as e:
        raise UnsupportedDocument(f"cannot read {path.name}: {e}") from e

    kind = sniff_kind(head)
    if kind is None or kind == "zip":
        raise UnsupportedDocument(
            f"{path.name} is not a PDF, a PowerPoint deck (.pptx), or a "
            "supported image (png, jpg, jpeg, webp, tiff). Export other "
            "documents and legacy .ppt decks to PDF."
        )

    try:
        doc = pymupdf.open(path)
    except Exception as e:
        raise UnsupportedDocument(f"cannot open {path.name}: {e}") from e

    if doc.needs_pass:
        doc.close()
        raise EncryptedDocument(
            f"{path.name} is password-protected. Remove the password and re-upload."
        )
    return Document(doc, "slides" if slides else kind)
```

- [ ] **Step 5: Document the new kind on `Document.rasterize`**

In `render.py`, extend `rasterize`'s docstring (line 293-299) by appending this paragraph before the code:

```
        ``kind == "slides"`` takes the vector path deliberately: LibreOffice
        emits vector PDF, so scaling a slide up is genuine detail at identical
        token cost. 16:9 slides are landscape and so take ``max_h = MAX_WIDTH``,
        rendering about 1120x630. No new raster constants.
```

- [ ] **Step 6: Run the full render and API suites**

Run: `uv run pytest tests/test_docparse_render.py tests/test_docparse_api.py -v`

Expected: all pass. `test_a_zip_is_rejected_by_open_document`, `test_sniff_kind_reports_a_zip_container` and `test_slides_flag_stamps_the_slides_kind` are new; `test_pptx_is_rejected_despite_its_extension` no longer exists (replaced by the zip test).

- [ ] **Step 7: Commit**

```bash
git add src/stratpoint_rag/docparse/render.py tests/test_docparse_render.py tests/test_docparse_api.py
git commit -m "feat(docparse): render.open_document learns the slides kind, reports zips"
```

---

### Task 3: Conversion — config, binary discovery, `ensure_pdf`

**Files:**
- Modify: `src/stratpoint_rag/docparse/slides.py`
- Modify: `src/stratpoint_rag/docparse/config.py` (add two functions, extend `__all__`)
- Modify: `tests/test_docparse_slides.py`
- Modify: `tests/test_docparse_config.py`
- Modify: `.envexample`

**Interfaces:**
- Consumes: `slides.is_pptx`, `slides.ConversionFailed` (Task 1)
- Produces:
  - `config.soffice_binary() -> str` — `""` when unset (auto-discover)
  - `config.soffice_timeout() -> int` — default `120`
  - `slides.Converter` — `Protocol` with `__call__(self, src: Path, outdir: Path) -> None`
  - `slides.find_soffice() -> str` — raises `RuntimeError` when not found
  - `slides.ensure_pdf(path: str | Path, *, convert: Converter | None = None) -> Path`
  - `slides.CONVERTED_NAME = "converted.pdf"`
  - Module-level `slides._soffice_convert` — the production `Converter`, monkeypatchable by name

- [ ] **Step 1: Write the failing config tests**

In `tests/test_docparse_config.py`, add `"SOFFICE_BINARY"` and `"SOFFICE_TIMEOUT"` to the `_ALL_VARS` tuple, and append these tests:

```python
def test_soffice_binary_defaults_to_empty(monkeypatch):
    """Blank means auto-discover; slides.find_soffice owns the search order."""
    monkeypatch.delenv("SOFFICE_BINARY", raising=False)
    assert config.soffice_binary() == ""


def test_soffice_binary_reads_the_env(monkeypatch):
    monkeypatch.setenv("SOFFICE_BINARY", "/opt/libreoffice/program/soffice")
    assert config.soffice_binary() == "/opt/libreoffice/program/soffice"


def test_soffice_timeout_defaults_to_120(monkeypatch):
    monkeypatch.delenv("SOFFICE_TIMEOUT", raising=False)
    assert config.soffice_timeout() == 120


def test_soffice_timeout_falls_back_on_garbage(monkeypatch):
    """A typo'd .env must not raise inside an upload request."""
    monkeypatch.setenv("SOFFICE_TIMEOUT", "two minutes")
    assert config.soffice_timeout() == 120
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_docparse_config.py -k soffice -v`

Expected: 4 errors — `AttributeError: module ... has no attribute 'soffice_binary'`

- [ ] **Step 3: Implement the config functions**

In `src/stratpoint_rag/docparse/config.py`, add `"soffice_binary"` and `"soffice_timeout"` to `__all__` (keep it alphabetically sorted), and append:

```python
def soffice_binary() -> str:
    """Explicit path to the LibreOffice binary; '' means auto-discover.

    LibreOffice is a hard dependency of the deck path but it is a system
    package, not a Python one, so no installer we ship puts it on PATH. It
    routinely is not on PATH on Windows at all. This is the escape hatch;
    ``slides.find_soffice`` owns the fallback search.
    """
    val = os.getenv("SOFFICE_BINARY")
    return val.strip() if val else ""


def soffice_timeout() -> int:
    """Seconds a single deck conversion may take before the child is killed.

    Same reasoning as VISION_TIMEOUT: a hung soffice would otherwise block an
    upload request indefinitely, and this converts that into one clean
    ConversionFailed. 120s is generous — a 40-slide deck converts in a few
    seconds — because the cost of clipping a merely slow conversion is a
    rejected upload, while the cost of the ceiling being loose is bounded.
    """
    return _int_env("SOFFICE_TIMEOUT", 120)
```

- [ ] **Step 4: Run the config tests**

Run: `uv run pytest tests/test_docparse_config.py -v`

Expected: all pass.

- [ ] **Step 5: Write the failing conversion tests**

Append to `tests/test_docparse_slides.py`:

```python
# ── conversion ──────────────────────────────────────────────────────────────


def _fake_converter(calls):
    """A Converter that records its calls and writes a plausible PDF."""

    def convert(src, outdir):
        calls.append((src, outdir))
        (outdir / f"{src.stem}.pdf").write_bytes(b"%PDF-1.7\n%%EOF\n")

    return convert


def test_ensure_pdf_converts_a_deck(deck):
    calls = []
    out = slides.ensure_pdf(deck, convert=_fake_converter(calls))

    assert out == deck.parent / slides.CONVERTED_NAME
    assert out.is_file()
    assert len(calls) == 1


def test_ensure_pdf_is_a_no_op_for_a_pdf(tmp_path):
    """Both call sites invoke it unconditionally, so a non-deck must pass
    straight through without spawning anything."""
    path = tmp_path / "brief.pdf"
    path.write_bytes(b"%PDF-1.7\n%%EOF\n")
    calls = []

    assert slides.ensure_pdf(path, convert=_fake_converter(calls)) == path
    assert calls == []


def test_ensure_pdf_caches_the_conversion(deck):
    """Called at upload and again at parse. The second call is a stat, not a
    ten-second subprocess."""
    calls = []
    convert = _fake_converter(calls)

    first = slides.ensure_pdf(deck, convert=convert)
    second = slides.ensure_pdf(deck, convert=convert)

    assert first == second
    assert len(calls) == 1


def test_ensure_pdf_reconverts_when_the_cache_is_empty(deck):
    """A zero-byte converted.pdf is a crashed previous run, not a cache hit."""
    (deck.parent / slides.CONVERTED_NAME).write_bytes(b"")
    calls = []

    slides.ensure_pdf(deck, convert=_fake_converter(calls))

    assert len(calls) == 1


def test_ensure_pdf_raises_when_nothing_was_produced(deck):
    """soffice returns 0 on inputs it silently declined, so the exit code is
    never the check — the output file is."""

    def convert(src, outdir):
        return None  # exits cleanly, writes nothing

    with pytest.raises(slides.ConversionFailed) as ex:
        slides.ensure_pdf(deck, convert=convert)
    assert deck.name in str(ex.value)


def test_ensure_pdf_raises_when_the_output_is_empty(deck):
    def convert(src, outdir):
        (outdir / f"{src.stem}.pdf").write_bytes(b"")

    with pytest.raises(slides.ConversionFailed):
        slides.ensure_pdf(deck, convert=convert)


def test_ensure_pdf_wraps_a_converter_crash(deck):
    """A subprocess failure must arrive as ConversionFailed, which app.py
    already maps to 400 — never as a raw OSError escaping to a 500."""

    def convert(src, outdir):
        raise OSError("soffice died")

    with pytest.raises(slides.ConversionFailed):
        slides.ensure_pdf(deck, convert=convert)


# ── the soffice command line ────────────────────────────────────────────────


def test_soffice_command_isolates_the_user_profile(monkeypatch, deck, tmp_path):
    """The regression guard for the silent failure.

    Without -env:UserInstallation, an already-running soffice — or a second
    concurrent upload — makes this invocation exit 0 having converted nothing.
    It looks exactly like success, which is why it is asserted here rather than
    trusted to a comment.
    """
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(slides, "find_soffice", lambda: "soffice")
    monkeypatch.setattr(slides.subprocess, "run", fake_run)

    slides._soffice_convert(deck, tmp_path)

    cmd = seen["cmd"]
    assert any(a.startswith("-env:UserInstallation=file://") for a in cmd)
    assert "--headless" in cmd
    assert "--convert-to" in cmd
    assert cmd[cmd.index("--convert-to") + 1] == "pdf"
    assert cmd[cmd.index("--outdir") + 1] == str(tmp_path)
    assert cmd[-1] == str(deck)
    assert seen["kwargs"]["timeout"] == 120


def test_soffice_convert_raises_on_a_timeout(monkeypatch, deck, tmp_path):
    """Under a hung conversion the upload must fail cleanly, not hang."""

    def fake_run(cmd, **kwargs):
        raise slides.subprocess.TimeoutExpired(cmd, 120)

    monkeypatch.setattr(slides, "find_soffice", lambda: "soffice")
    monkeypatch.setattr(slides.subprocess, "run", fake_run)

    with pytest.raises(slides.ConversionFailed) as ex:
        slides._soffice_convert(deck, tmp_path)
    assert "120" in str(ex.value)


def test_find_soffice_prefers_the_configured_binary(monkeypatch, tmp_path):
    binary = tmp_path / "soffice"
    binary.write_text("#!/bin/sh\n")
    monkeypatch.setenv("SOFFICE_BINARY", str(binary))

    assert slides.find_soffice() == str(binary)


def test_find_soffice_raises_when_absent(monkeypatch):
    """RuntimeError, not ConversionFailed: the file is fine, the server is
    misconfigured. api/app.py maps that to 503."""
    monkeypatch.setenv("SOFFICE_BINARY", "")
    monkeypatch.setattr(slides.shutil, "which", lambda name: None)
    monkeypatch.setattr(slides, "_WINDOWS_CANDIDATES", ())

    with pytest.raises(RuntimeError) as ex:
        slides.find_soffice()
    assert "LibreOffice" in str(ex.value)
```

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run pytest tests/test_docparse_slides.py -v`

Expected: the 6 Task-1 tests pass; the 12 new ones fail with `AttributeError: module 'stratpoint_rag.docparse.slides' has no attribute 'ensure_pdf'` (and similar).

- [ ] **Step 7: Implement conversion in `slides.py`**

Replace the import block and append the implementation. New imports at the top of `slides.py`:

```python
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Protocol

from stratpoint_rag.docparse import config
from stratpoint_rag.docparse.render import UnsupportedDocument
```

Extend `__all__`:

```python
__all__ = [
    "CONVERTED_NAME",
    "ConversionFailed",
    "Converter",
    "ensure_pdf",
    "find_soffice",
    "is_pptx",
]
```

Add the constants beside `_PPTX_MARKER`:

```python
# A fixed name inside the upload's own <session_id>/<upload_id>/ directory, so
# it is scoped by upload_id, swept by store.py's existing TTL and boot purge
# with no change, and cannot collide across uploads.
CONVERTED_NAME = "converted.pdf"

# LibreOffice is routinely not on PATH on Windows even when installed.
_WINDOWS_CANDIDATES = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
)
```

Append the rest:

```python
class Converter(Protocol):
    """The testability seam, mirroring ``stratpoint_crawl``'s ``Fetcher``.

    Anything that turns ``src`` into ``<outdir>/<src.stem>.pdf`` satisfies it.
    Tests inject a fake that writes a stub file, which is what lets the entire
    deck path be exercised on a machine with no LibreOffice installed.
    """

    def __call__(self, src: Path, outdir: Path) -> None: ...


def find_soffice() -> str:
    """Locate the LibreOffice binary.

    Raises ``RuntimeError`` — not ``ConversionFailed`` — when it is absent: the
    uploaded file is perfectly fine and the server is misconfigured, so this is
    a 503, matching how a missing API key is signalled elsewhere in docparse.
    """
    configured = config.soffice_binary()
    if configured:
        return configured
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in _WINDOWS_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    raise RuntimeError(
        "LibreOffice was not found. It is required to accept .pptx decks; "
        "install it, or set SOFFICE_BINARY to the soffice executable."
    )


def _soffice_convert(src: Path, outdir: Path) -> None:
    """Run headless LibreOffice once. The production :class:`Converter`.

    ``-env:UserInstallation`` is mandatory, not hygiene. LibreOffice keeps a
    single user profile and refuses to run two instances against it, so without
    a private one per invocation an already-running soffice — or a second
    concurrent upload — makes this **exit 0 having converted nothing**. That is
    the classic headless failure and it is indistinguishable from success
    anywhere except the missing output file, which is why ``ensure_pdf`` checks
    for the file rather than the return code.
    """
    binary = find_soffice()
    with tempfile.TemporaryDirectory(prefix="soffice-profile-") as profile:
        cmd = [
            binary,
            f"-env:UserInstallation={Path(profile).as_uri()}",
            "--headless",
            "--norestore",
            "--convert-to",
            "pdf",
            "--outdir",
            str(outdir),
            str(src),
        ]
        timeout = config.soffice_timeout()
        log.info("converting %s with %s", src.name, binary)
        try:
            result = subprocess.run(
                cmd, capture_output=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired as e:
            raise ConversionFailed(
                f"converting {src.name} exceeded {timeout}s"
            ) from e
        if result.returncode != 0:
            # Logged, not raised: ensure_pdf's file check is the real verdict,
            # and soffice returns 0 on inputs it silently declined anyway.
            log.warning(
                "soffice exited %d on %s: %s",
                result.returncode,
                src.name,
                (result.stderr or b"")[:400].decode("utf-8", "replace"),
            )


def ensure_pdf(path: str | Path, *, convert: Converter | None = None) -> Path:
    """Return a PDF for ``path``, converting it first if it is a deck.

    Idempotent, and a no-op for anything that is not a ``.pptx`` — both
    ``/upload``'s page count and ``transcribe_document`` call it
    unconditionally, and the second call must cost a ``stat`` rather than a
    second conversion. The result is cached at ``<parent>/converted.pdf``.

    ``convert`` is injected by tests; production uses ``_soffice_convert``.
    """
    path = Path(path)
    if not is_pptx(path):
        return path

    cached = path.parent / CONVERTED_NAME
    if cached.is_file() and cached.stat().st_size > 0:
        return cached

    convert = convert or _soffice_convert
    with tempfile.TemporaryDirectory(prefix="soffice-out-") as tmp:
        outdir = Path(tmp)
        try:
            convert(path, outdir)
        except (ConversionFailed, RuntimeError):
            # RuntimeError passes through UNTOUCHED. It means LibreOffice is not
            # installed, which api/app.py maps to 503 — wrapping it as
            # ConversionFailed would report a 400 and tell the visitor to fix a
            # deck that is perfectly fine.
            raise
        except Exception as e:
            # Never let a raw OSError escape to a 500: app.py's boundary maps
            # ConversionFailed to a 400 with a message the visitor can act on.
            raise ConversionFailed(f"cannot convert {path.name}: {e}") from e

        produced = outdir / f"{path.stem}.pdf"
        if not produced.is_file() or produced.stat().st_size == 0:
            raise ConversionFailed(
                f"LibreOffice produced no PDF for {path.name}. The deck may be "
                "corrupt or password-protected."
            )
        shutil.move(str(produced), str(cached))

    return cached
```

- [ ] **Step 8: Run the slides suite**

Run: `uv run pytest tests/test_docparse_slides.py -v`

Expected: 18 passed.

- [ ] **Step 9: Mirror the new variables into `.envexample`**

In `.envexample`, in the "docparse — uploads + parsing" comment block, add these two lines to the defaults list after `DOCPARSE_TEXT_LAYER_MIN_CHARS`:

```
#   SOFFICE_BINARY                (blank = auto-discover soffice/libreoffice on PATH)
#   SOFFICE_TIMEOUT               120  (seconds a single .pptx conversion may take)
# LibreOffice is a REQUIRED system package for .pptx uploads; it is not a
# Python dependency, so no installer here puts it on PATH. On Windows it
# usually is not on PATH at all — set SOFFICE_BINARY there.
```

and add the two blank assignments after `DOCPARSE_TEXT_LAYER_MIN_CHARS=`:

```
SOFFICE_BINARY=
SOFFICE_TIMEOUT=
```

- [ ] **Step 10: Record the derived file in `store.py`'s layout docstring**

`store.py`'s module docstring draws the on-disk layout, and it is now wrong —
a deck upload has a fourth file. In `src/stratpoint_rag/docparse/store.py`,
extend the layout block:

```
    <UPLOAD_DIR>/<session_id>/<upload_id>/
        meta.json          filename + sha256
        <sanitised name>   the uploaded bytes
        converted.pdf      decks only: the LibreOffice-derived PDF (slides.py)
        transcription.md   the hop-1 artifact
```

No code changes here. `converted.pdf` lives inside the `upload_id` directory
precisely so `sweep`, `purge_all` and `delete_upload` already reach it — verify
by reading them, and if any of the three enumerates filenames rather than
removing the directory tree, that is a bug this step must fix.

- [ ] **Step 11: Commit**

```bash
git add src/stratpoint_rag/docparse/slides.py src/stratpoint_rag/docparse/config.py src/stratpoint_rag/docparse/store.py tests/test_docparse_slides.py tests/test_docparse_config.py .envexample
git commit -m "feat(docparse): headless LibreOffice conversion with a cached derived PDF"
```

---

### Task 4: Pipeline — `open_brief`, forced vision, split provenance

**Files:**
- Modify: `src/stratpoint_rag/docparse/slides.py` (add `open_brief`)
- Modify: `src/stratpoint_rag/docparse/transcribe.py:282-298` (`_needs_vision`), `:490-566` (`transcribe_document`)
- Modify: `src/stratpoint_rag/docparse/__init__.py` (re-export)
- Modify: `tests/test_docparse_slides.py`, `tests/test_docparse_transcribe.py`

**Interfaces:**
- Consumes: `slides.ensure_pdf`, `slides.Converter` (Task 3); `render.open_document(path, *, slides=...)` (Task 2)
- Produces:
  - `slides.open_brief(path: str | Path, *, convert: Converter | None = None) -> render.Document`
  - `_needs_vision` returns `True` for `doc.kind == "slides"`
  - `transcribe_document` unchanged in signature; it now opens via `slides.open_brief`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_docparse_slides.py`:

```python
# ── the pipeline entry point ────────────────────────────────────────────────


def test_open_brief_marks_a_converted_deck_as_slides(deck, monkeypatch):
    """The whole feature turns on this flag: without it the converted PDF's
    text layer sends every slide down the free text route."""
    import pymupdf

    def convert(src, outdir):
        doc = pymupdf.open()
        doc.new_page(width=720, height=405)
        doc.save(outdir / f"{src.stem}.pdf")
        doc.close()

    with slides.open_brief(deck, convert=convert) as doc:
        assert doc.kind == "slides"
        assert doc.page_count == 1


def test_open_brief_leaves_a_pdf_alone(tmp_path):
    import pymupdf

    path = tmp_path / "brief.pdf"
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    doc.save(path)
    doc.close()

    with slides.open_brief(path) as opened:
        assert opened.kind == "pdf"
```

Append to `tests/test_docparse_transcribe.py`:

```python
# ── decks: one image per slide, never a text extract ────────────────────────


def _slide_deck(tmp_path, text="Migrate the platform to Kubernetes on AWS."):
    """A .pptx whose fake conversion yields a PDF with a FAT text layer.

    The text layer matters: it is what would send every slide down the free
    text route if the slides kind were not forcing vision. A deck whose
    converted PDF had no text would pass the test for the wrong reason.
    """
    import zipfile

    import pymupdf

    path = tmp_path / "clientdeck.pptx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ppt/presentation.xml", "<presentation/>")

    def convert(src, outdir):
        doc = pymupdf.open()
        for n in (1, 2):
            page = doc.new_page(width=720, height=405)
            page.insert_text((40, 60), f"Slide {n}: {text}", fontsize=14)
            page.insert_text((40, 90), text * 4, fontsize=10)
        doc.save(outdir / f"{src.stem}.pdf")
        doc.close()

    return path, convert


def test_every_slide_takes_the_vision_route(tmp_path, monkeypatch):
    """No text extracts. Both slides carry a text layer far over
    text_layer_min_chars and both must still be rasterized."""
    from stratpoint_rag.docparse import slides as slides_mod

    path, convert = _slide_deck(tmp_path)
    monkeypatch.setattr(slides_mod, "_soffice_convert", convert)

    result = transcribe_document(path, vision=FakeVisionClient())

    assert result.pages_total == 2
    assert result.pages_via_vision == 2


def test_deck_provenance_names_the_original_not_the_derived_pdf(
    tmp_path, monkeypatch
):
    """The visitor uploaded a .pptx. A hash of a PDF they never saw cannot be
    checked against anything they hold."""
    import hashlib

    from stratpoint_rag.docparse import slides as slides_mod

    path, convert = _slide_deck(tmp_path)
    monkeypatch.setattr(slides_mod, "_soffice_convert", convert)

    result = transcribe_document(path, vision=FakeVisionClient())

    assert result.source_file == "clientdeck.pptx"
    assert result.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_docparse_slides.py -k open_brief tests/test_docparse_transcribe.py -k "slide or deck" -v`

Expected: `AttributeError: module 'stratpoint_rag.docparse.slides' has no attribute 'open_brief'`, and the transcribe tests fail with `UnsupportedDocument` (the pptx never gets converted).

- [ ] **Step 3: Implement `open_brief`**

Append to `slides.py`, and add `"open_brief"` to `__all__`:

```python
def open_brief(path: str | Path, *, convert: Converter | None = None):
    """Open any supported brief, converting a deck to PDF first.

    The single entry point for the pipeline: ``/upload``'s page count and
    ``transcribe_document`` both go through here, so neither has to know
    whether a deck is involved and neither can forget the ``slides`` flag.

    Imported here rather than at module scope only to keep the import graph
    one-directional and obvious — ``render`` knows nothing about LibreOffice.
    """
    from stratpoint_rag.docparse import render

    path = Path(path)
    render_path = ensure_pdf(path, convert=convert)
    return render.open_document(render_path, slides=render_path != path)
```

Note: `UnsupportedDocument` is already imported at module scope from `render`,
so the function-level import is a readability choice, not a cycle fix. Move it
to module scope alongside the existing import if you prefer — there is no cycle
either way.

- [ ] **Step 4: Implement the `_needs_vision` clause**

In `transcribe.py`, replace lines 292-293:

```python
    if doc.kind == "image":
        return True  # no text layer exists to check
```

with:

```python
    if doc.kind == "image":
        return True  # no text layer exists to check
    if doc.kind == "slides":
        # A converted deck carries a PERFECT text layer — it is the real slide
        # text, not OCR — so every slide would take the free text route and the
        # deck feature would do nothing. That is the wrong trade here: a slide
        # is mostly picture, and the architecture diagram on it holds
        # constraints (on-prem, a named cloud, microservices) that exist
        # nowhere in its words. The text layer is still read and handed to the
        # worker, but only as the figure pass's novelty baseline.
        return True
```

- [ ] **Step 5: Implement the `transcribe_document` wiring**

In `transcribe.py`, replace line 506:

```python
    with render.open_document(path) as doc:
```

with:

```python
    # slides.open_brief, not render.open_document: a .pptx is converted to PDF
    # first. sha256 and source_file above are read from the ORIGINAL upload, so
    # the artifact names the file the visitor actually sent.
    with slides.open_brief(path) as doc:
```

Add the import to `transcribe.py`'s import block:

```python
from stratpoint_rag.docparse import slides
```

(Check the existing block — it already imports `render` and `config` in this style; follow whichever form is there.)

- [ ] **Step 6: Re-export from the package**

In `src/stratpoint_rag/docparse/__init__.py`, beside the existing
`from stratpoint_rag.docparse.render import EncryptedDocument, UnsupportedDocument`
(line 59), add:

```python
from stratpoint_rag.docparse.slides import ConversionFailed
```

and add `"ConversionFailed"` to that module's `__all__`.

- [ ] **Step 7: Run the docparse suite**

Run: `uv run pytest tests/test_docparse_slides.py tests/test_docparse_transcribe.py tests/test_docparse_render.py -v`

Expected: all pass, including the pre-existing `test_unopenable_file_aborts_rather_than_soft_failing` (its garbage `deck.pptx` is not a valid zip, so `is_pptx` is False, `ensure_pdf` passes it through, and `open_document` rejects the zip exactly as before).

- [ ] **Step 8: Commit**

```bash
git add src/stratpoint_rag/docparse/slides.py src/stratpoint_rag/docparse/transcribe.py src/stratpoint_rag/docparse/__init__.py tests/test_docparse_slides.py tests/test_docparse_transcribe.py
git commit -m "feat(docparse): route every slide through vision, keep deck provenance"
```

---

### Task 5: API — accept decks at `/upload`, 503 on a missing binary

**Files:**
- Modify: `src/stratpoint_rag/api/app.py:337-341` (`_page_count`), `:184-188` (the upload handler's try block)
- Modify: `tests/test_docparse_api.py`

**Interfaces:**
- Consumes: `slides.open_brief` (Task 4), `slides.ConversionFailed` (Task 1)
- Produces: `/upload` returns `UploadResponse.pages` = slide count for a deck; returns 503 when LibreOffice is absent.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_docparse_api.py`:

```python
def _pptx_bytes():
    """A minimal deck. /upload only needs it to look like one to is_pptx."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ppt/presentation.xml", "<presentation/>")
        z.writestr("ppt/slides/slide1.xml", "<sld/>")
    return buf.getvalue()


def test_upload_accepts_a_deck_and_counts_slides(monkeypatch):
    from stratpoint_rag.docparse import slides

    def convert(src, outdir):
        import pymupdf

        doc = pymupdf.open()
        for _ in range(3):
            doc.new_page(width=720, height=405)
        doc.save(outdir / f"{src.stem}.pdf")
        doc.close()

    monkeypatch.setattr(slides, "_soffice_convert", convert)

    r = _upload(_pptx_bytes(), name="brief.pptx")

    assert r.status_code == 200
    assert r.json()["pages"] == 3


def test_upload_rejects_a_deck_libreoffice_cannot_convert(monkeypatch):
    from stratpoint_rag.docparse import slides

    def convert(src, outdir):
        return None  # exits cleanly, produces nothing

    monkeypatch.setattr(slides, "_soffice_convert", convert)

    r = _upload(_pptx_bytes(), name="corrupt.pptx")

    assert r.status_code == 400
    assert "corrupt.pptx" in r.json()["detail"]


def test_upload_reports_503_when_libreoffice_is_missing(monkeypatch):
    """The file is fine; the server is misconfigured. 400 would tell the
    visitor to fix their deck, which is the wrong instruction."""
    from stratpoint_rag.docparse import slides

    def missing():
        raise RuntimeError("LibreOffice was not found.")

    monkeypatch.setattr(slides, "find_soffice", missing)

    r = _upload(_pptx_bytes(), name="brief.pptx")

    assert r.status_code == 503
    assert "LibreOffice" in r.json()["detail"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_docparse_api.py -k "deck or libreoffice" -v`

Expected: the first two fail with 400 (`is not a PDF...` — the deck is never converted), the third fails with a 500.

- [ ] **Step 3: Implement `_page_count`**

In `app.py`, replace `_page_count` (lines 337-341) with:

```python
def _page_count(path) -> int:
    """Open just far enough to count pages, rejecting encrypted files here
    rather than letting them fail confusingly deep in the page loop.

    Goes through ``slides.open_brief``, so a .pptx is converted to PDF on this
    call and the derived file is cached inside the upload's own directory. The
    confirmation dialog needs a real slide count, and you cannot have one
    without opening the deck — which is the whole reason conversion happens at
    upload rather than at parse.
    """
    with slides.open_brief(path) as doc:
        return doc.page_count
```

Add the import beside the existing docparse imports at the top of `app.py`:

```python
from stratpoint_rag.docparse import slides
```

- [ ] **Step 4: Implement the 503 handler**

In `app.py`, replace the upload handler's try block (lines 184-188):

```python
    try:
        pages = _page_count(record.path)
    except (UnsupportedDocument, EncryptedDocument) as ex:
        store.delete_upload(session_id, upload_id)  # never keep what we rejected
        raise HTTPException(status_code=400, detail=str(ex))
```

with:

```python
    try:
        pages = _page_count(record.path)
    except (UnsupportedDocument, EncryptedDocument) as ex:
        store.delete_upload(session_id, upload_id)  # never keep what we rejected
        raise HTTPException(status_code=400, detail=str(ex))
    except RuntimeError as ex:
        # Setup problems, not file problems — today that means LibreOffice is
        # not installed. 400 would tell the visitor to fix a deck that is
        # perfectly fine. Matches how /chat and /upload/{id}/parse already
        # signal a missing API key.
        store.delete_upload(session_id, upload_id)
        raise HTTPException(status_code=503, detail=str(ex))
```

- [ ] **Step 5: Run the API suite**

Run: `uv run pytest tests/test_docparse_api.py -v`

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/stratpoint_rag/api/app.py tests/test_docparse_api.py
git commit -m "feat(api): accept .pptx uploads, 503 when LibreOffice is absent"
```

---

### Task 6: UI, documentation, and the live conversion test

**Files:**
- Modify: `src/stratpoint_rag/ui/app.py:15` (`ACCEPTED_TYPES`), `:52` (label)
- Modify: `src/stratpoint_rag/docparse/render.py` (module docstring note)
- Modify: `CLAUDE.md`, `README.md`, `docs/deploy-lxc-6gb-no-docker.md`
- Modify: `pyproject.toml` (dev dependency group)
- Modify: `tests/test_docparse_slides.py` (integration test)

**Interfaces:**
- Consumes: everything from Tasks 1-5.
- Produces: no new code interfaces.

- [ ] **Step 1: Widen the uploader**

In `src/stratpoint_rag/ui/app.py`, replace line 15:

```python
ACCEPTED_TYPES = ["pdf", "png", "jpg", "jpeg", "webp", "tiff"]
```

with:

```python
# A client-side convenience only. /upload is a real HTTP endpoint reachable
# without Streamlit, so the authoritative check is content-based, at the API.
ACCEPTED_TYPES = ["pdf", "pptx", "png", "jpg", "jpeg", "webp", "tiff"]
```

and replace the label on line 52:

```python
        "Drop a PDF or image", type=ACCEPTED_TYPES, label_visibility="collapsed"
```

with:

```python
        "Drop a PDF, a PowerPoint deck, or an image",
        type=ACCEPTED_TYPES,
        label_visibility="collapsed",
```

- [ ] **Step 2: Run the UI tests**

Run: `uv run pytest tests/test_docparse_ui_uploader.py tests/test_docparse_ui_state.py tests/test_docparse_ui_attachments.py -v`

Expected: all pass. If a test asserts the old label string verbatim, update that assertion to the new label — it is the same deliberate change.

- [ ] **Step 3: Add the dev-only fixture dependency**

`python-pptx` is needed **only** to build a real deck for the integration test —
a hand-written minimal OOXML package is fragile enough that LibreOffice may
reject it, which would fail the test for the wrong reason. It is never imported
by `src/`. Add it to the dev group in `pyproject.toml` beside `pytest`,
`pytest-asyncio` and `respx`:

```toml
python-pptx = ">=1.0"
```

Match the surrounding syntax exactly — the group is `[dependency-groups]`, so
entries are plain strings in a list, e.g. `"python-pptx>=1.0",`. Then:

```bash
uv sync
```

- [ ] **Step 4: Write the integration test**

Append to `tests/test_docparse_slides.py`:

```python
# ── live conversion ─────────────────────────────────────────────────────────


@pytest.mark.integration
def test_real_libreoffice_converts_a_real_deck(tmp_path):
    """The only test here that spawns LibreOffice. Deselected by default via
    pyproject's addopts, like the crawler's live test.

    Guards what the fake converter structurally cannot: that the command line
    is one LibreOffice actually accepts, and that a real deck yields one PDF
    page per slide.
    """
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    for n in (1, 2, 3):
        slide = prs.slides.add_slide(prs.slide_layouts[5])
        slide.shapes.title.text = f"Slide {n}"
        slide.shapes.add_textbox(
            Inches(1), Inches(2), Inches(6), Inches(1)
        ).text_frame.text = "Migrate to Kubernetes on AWS."
    path = tmp_path / "live.pptx"
    prs.save(path)

    with slides.open_brief(path) as doc:
        assert doc.kind == "slides"
        assert doc.page_count == 3
        image = doc.rasterize(0)

    assert image.startswith(b"\xff\xd8\xff")  # JPEG
    assert len(image) > 1000
```

- [ ] **Step 5: Run it against the real binary**

Run: `uv run pytest tests/test_docparse_slides.py -m integration -v`

Expected: PASS if LibreOffice is installed. If it is not, the test errors with
the `RuntimeError` from `find_soffice` — install LibreOffice, or set
`SOFFICE_BINARY`, since it is now a required dependency. Confirm the default
run still excludes it:

Run: `uv run pytest tests/test_docparse_slides.py -v`
Expected: the integration test is deselected; everything else passes.

- [ ] **Step 6: Correct `render.py`'s obsolete docstring**

The `sniff_kind` docstring was already rewritten in Task 2. Now update the
**module** docstring at the top of `render.py` (lines 1-9) so the file's opening
paragraph does not still read as PDF-and-image-only. Replace the first paragraph:

```
"""PDF/image handling: validate, count pages, read the text layer, rasterize.
```

with:

```
"""PDF/image handling: validate, count pages, read the text layer, rasterize.

Decks arrive here already converted to PDF — ``slides.py`` owns LibreOffice and
nothing about that reaches this module beyond the ``slides=`` flag on
:func:`open_document`, which only changes ``Document.kind``.
```

- [ ] **Step 7: Update `CLAUDE.md`**

In the "Document parsing (`stratpoint_rag.docparse`)" section, add this bullet
to "Key design decisions (read before editing)", placed immediately before the
"**The text layer is the cost saver**" bullet — the two must be read together,
because the deck rule is the documented exception to it:

```markdown
- **A deck is converted, then rasterized — never text-extracted.** `.pptx` is
  accepted since 2026-08-14, reversing the earlier "export it to PDF" rule.
  `slides.py` shells out to headless LibreOffice, caches `converted.pdf` inside
  the upload's own directory, and `slides.open_brief` is the single entry point
  both `/upload`'s page count and `transcribe_document` use. Three things are
  load-bearing. `-env:UserInstallation` is **mandatory**: without a private
  profile per invocation, an already-running soffice makes the call exit 0
  having converted nothing, which is indistinguishable from success — so the
  output *file*, never the return code, is the verdict. `Document.kind` is
  `"slides"`, which forces `_needs_vision` to True for every page: the converted
  PDF carries a perfect text layer (real slide text, not OCR) and without that
  clause every slide takes the free text route and the feature does nothing. And
  provenance is split — `sha256`/`source_file` name the original `.pptx`, while
  the pages come from the derived PDF the visitor never saw. The text layer is
  still read, but only as the figure pass's novelty baseline. **LibreOffice is
  now a hard dependency** (a ~400 MB install on the 6 GB LXC); that cost, and
  the fact that every deck is 100% vision calls where a digital PDF is 0%, are
  the price of the reversal. See
  `docs/superpowers/specs/2026-08-14-pptx-slide-rasterization-design.md`.
```

Also correct the `docparse` line in the "Repository layout" tree if it enumerates
supported input formats, and remove any remaining claim that `.pptx` is
unsupported.

Then add this to the **"Known limitation, deferred by decision"** area of the
same section, beside the existing prompt-injection paragraph — the accepted risk
from the spec, which must not live only in a spec file:

```markdown
**Second accepted risk: LibreOffice parses attacker-controlled input.** Since
`.pptx` support landed, every deck upload runs a large C++ office suite with a
long history of parser CVEs over a file a stranger chose. Bounded by
`upload_max_bytes` (25 MB), `SOFFICE_TIMEOUT`, a throwaway user profile, and the
non-root API user — but it is a materially larger attack surface than PyMuPDF
alone, and it was accepted knowingly rather than overlooked.
```

- [ ] **Step 8: Update `README.md`**

LibreOffice is a system package that none of the three documented toolchains
install, so it needs a line in each. In the setup section, after the
`playwright install chromium` step in **all three** paths (uv, pip+venv, and the
conda/mamba path), add an equivalent of:

```markdown
**LibreOffice (required for `.pptx` uploads).** Not a Python package — install it
with your system package manager:

```bash
# Debian/Ubuntu (including the LXC target)
sudo apt-get install -y libreoffice-impress

# macOS
brew install --cask libreoffice

# Windows: install from libreoffice.org, then point SOFFICE_BINARY at
# C:\Program Files\LibreOffice\program\soffice.exe (it is not on PATH).
```

`libreoffice-impress` rather than the full `libreoffice` metapackage: it pulls
the presentation filters and the headless core without Writer, Calc and Base.
```

Also add `.pptx` to wherever the README lists accepted upload formats.

- [ ] **Step 9: Update `docs/deploy-lxc-6gb-no-docker.md`**

Add `libreoffice-impress` to the system-package install step, with a note that
it is the largest single package in the deployment and the reason the earlier
design rejected deck support. State the measured installed size after running
the install so the number in the doc is real, not guessed:

```bash
sudo apt-get install -y libreoffice-impress
dpkg-query -Wf '${Installed-Size}\t${Package}\n' | sort -rn | head -20
```

- [ ] **Step 10: Run the whole suite**

Run: `uv run pytest`

Expected: all pass, integration deselected. Then confirm nothing else in the
repo still claims decks are unsupported:

Run: `git grep -in "export .*to pdf\|pptx" -- src README.md CLAUDE.md docs`

Review each hit; every remaining one should describe the *new* behaviour or be
about legacy `.ppt`.

- [ ] **Step 11: Commit**

```bash
git add src/stratpoint_rag/ui/app.py src/stratpoint_rag/docparse/render.py CLAUDE.md README.md docs/deploy-lxc-6gb-no-docker.md pyproject.toml uv.lock tests/test_docparse_slides.py
git commit -m "feat: accept PowerPoint decks end to end; document LibreOffice as required"
```

---

## Verification

After all six tasks:

```bash
uv run pytest                      # full unit suite, integration deselected
uv run pytest -m integration       # live LibreOffice + live crawl
```

Then a manual end-to-end check, which is the only thing that exercises the real
binary, the real vision model and the real UI together:

1. `uv run uvicorn stratpoint_rag.api.app:app` and `uv run streamlit run src/stratpoint_rag/ui/app.py`
2. Drop a real `.pptx` with at least one architecture diagram into the uploader.
3. Confirm the confirmation dialog reports the true slide count.
4. Confirm parsing completes and "View transcription" shows one `## Page N`
   block per slide, each marked `source: vision` in its provenance comment —
   **no page should read `source: text`**. That marker is the end-to-end proof
   of the "no text extracts" constraint.
5. Confirm the diagram slide's transcription describes the diagram rather than
   listing its labels.
