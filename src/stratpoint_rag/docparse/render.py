"""PDF/image handling: validate, count pages, read the text layer, rasterize.

Every PyMuPDF call in the project lives here. PyMuPDF is AGPL unless
commercially licensed; keeping rendering behind this one module is what makes a
swap to pypdfium2 (BSD/Apache, also renders + extracts text) a contained
change rather than a sweep.

The tile budget drives the rasterization caps — see MAX_WIDTH below.
"""

from __future__ import annotations

from pathlib import Path
from types import TracebackType

import pymupdf

__all__ = [
    "MAX_HEIGHT_PORTRAIT",
    "MAX_WIDTH",
    "Document",
    "EncryptedDocument",
    "JPEG_QUALITY",
    "UnsupportedDocument",
    "open_document",
    "sniff_kind",
]


# The vision endpoint bills exactly 1,601 tokens per tile + 27 overhead, and
# hard-caps at 4 tiles. Everything beyond ~1120px on the long edge is silently
# discarded — you pay upload time for pixels the model never sees. Worse, on a
# synthetic invoice page 2240x2912 (identical 6,431-token cost to 1120x1456)
# dropped the entire Overview body and only summarized. Higher resolution is
# not free and not better; it is the same price and worse.
MAX_WIDTH = 1120

# Portrait pages get the taller box: still 4 tiles, materially more legible.
MAX_HEIGHT_PORTRAIT = 1456

# JPEG, never PNG for pages and scans — ~25x the bytes for zero accuracy gain.
JPEG_QUALITY = 85

# Below this fraction of the page area, an embedded image is decoration (a
# logo, a rule) rather than a diagram worth spending a vision call on.
_LARGE_IMAGE_AREA_RATIO = 0.15

_PDF_MAGIC = b"%PDF-"
_IMAGE_MAGICS = (
    b"\x89PNG\r\n\x1a\n",  # png
    b"\xff\xd8\xff",  # jpeg
    b"II*\x00",  # tiff, little-endian
    b"MM\x00*",  # tiff, big-endian
)


class UnsupportedDocument(Exception):
    """The bytes are not a PDF or a supported image."""


class EncryptedDocument(Exception):
    """A password-protected PDF. Rejected at the boundary, not mid-page-loop."""


def sniff_kind(head: bytes) -> str | None:
    """Classify a file by magic bytes: 'pdf', 'image', or None.

    Content decides, never the extension. ``st.file_uploader``'s ``type=`` is a
    client-side filter, and ``/upload`` is a real HTTP endpoint reachable
    without Streamlit.

    .pptx/.docx are deliberately unsupported: PyMuPDF cannot open them,
    LibreOffice headless is a ~400 MB root install on a 6 GB LXC, and
    python-pptx is text-only — it misses every diagram and architecture slide,
    which is exactly where requirements live. "Export your deck to PDF" is a
    five-second ask.
    """
    if head.startswith(_PDF_MAGIC):
        return "pdf"
    if any(head.startswith(m) for m in _IMAGE_MAGICS):
        return "image"
    # WEBP is RIFF-framed: "RIFF" <4-byte size> "WEBP".
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image"
    return None


class Document:
    """An opened brief. One page-indexed surface over PyMuPDF.

    Use via :func:`open_document`; supports the context-manager protocol so the
    underlying file handle is always released.
    """

    def __init__(self, doc: pymupdf.Document, kind: str) -> None:
        self._doc = doc
        self.kind = kind

    @property
    def page_count(self) -> int:
        return self._doc.page_count

    def page_text(self, index: int) -> str:
        """The embedded text layer for one page ('' for images and scans)."""
        return self._doc[index].get_text()

    def page_has_large_image(self, index: int) -> bool:
        """True when the page carries an image big enough to be a diagram.

        Architecture slides carry real constraints — on-prem, specific clouds,
        microservices — that exist *only* as boxes and arrows, so a page with
        both text and a diagram still needs the vision path.
        """
        page = self._doc[index]
        page_area = abs(page.rect.width * page.rect.height)
        if not page_area:
            return False
        for block in page.get_image_info():
            bbox = pymupdf.Rect(block["bbox"])
            if abs(bbox.width * bbox.height) / page_area >= _LARGE_IMAGE_AREA_RATIO:
                return True
        return False

    @staticmethod
    def _native_zoom(page: pymupdf.Page, width_pt: float) -> float:
        """Pixels-per-point of the page's embedded image (1.0 if unknown)."""
        info = page.get_image_info()
        if not info or not width_pt:
            return 1.0
        return info[0]["width"] / width_pt

    def rasterize(self, index: int) -> bytes:
        """Render one page to a JPEG sized for the tile budget.

        PDF pages are scaled *up* to fill the budget when small — they are
        vector, so a higher zoom is genuinely more detail at identical token
        cost. Bare raster images are only ever scaled down; upscaling a raster
        adds no information the model can use.
        """
        if not 0 <= index < self.page_count:
            raise IndexError(f"page {index} out of range (0..{self.page_count - 1})")

        page = self._doc[index]
        width, height = page.rect.width, page.rect.height
        max_h = MAX_HEIGHT_PORTRAIT if height > width else MAX_WIDTH
        zoom = min(MAX_WIDTH / width, max_h / height)
        if self.kind == "image":
            # PyMuPDF sizes an image page in POINTS from the file's DPI, so
            # zoom=1.0 is not native resolution — a 96-dpi PNG would render at
            # 0.75x and silently lose a quarter of its pixels. Cap the zoom at
            # the image's true pixel density instead.
            zoom = min(zoom, self._native_zoom(page, width))

        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        if pix.colorspace is None or pix.colorspace.n != 3:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)  # JPEG needs RGB, no alpha
        return pix.tobytes(output="jpeg", jpg_quality=JPEG_QUALITY)

    def close(self) -> None:
        self._doc.close()

    def __enter__(self) -> Document:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def open_document(path: str | Path) -> Document:
    """Validate and open a brief.

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
    if kind is None:
        raise UnsupportedDocument(
            f"{path.name} is not a PDF or a supported image "
            "(png, jpg, jpeg, webp, tiff). Export decks and documents to PDF."
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
    return Document(doc, kind)
