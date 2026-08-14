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
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Protocol

from stratpoint_rag.docparse import config
from stratpoint_rag.docparse.render import UnsupportedDocument

log = logging.getLogger(__name__)

__all__ = [
    "CONVERTED_NAME",
    "ConversionFailed",
    "Converter",
    "ensure_pdf",
    "find_soffice",
    "is_pptx",
    "open_brief",
]

# The part every Office Open XML presentation carries and no other OOXML
# format does. A .docx has word/document.xml, a .xlsx has xl/workbook.xml.
_PPTX_MARKER = "ppt/presentation.xml"

# A fixed name inside the upload's own <session_id>/<upload_id>/ directory, so
# it is scoped by upload_id, swept by store.py's existing TTL and boot purge
# with no change, and cannot collide across uploads.
CONVERTED_NAME = "converted.pdf"

# LibreOffice is routinely not on PATH on Windows even when installed.
_WINDOWS_CANDIDATES = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
)


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
