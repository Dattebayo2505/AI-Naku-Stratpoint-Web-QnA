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
