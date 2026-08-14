"""PDF/image handling: validate, count pages, read the text layer, rasterize.

Every PyMuPDF call in the project lives here. PyMuPDF is AGPL unless
commercially licensed; keeping rendering behind this one module is what makes a
swap to pypdfium2 (BSD/Apache, also renders + extracts text) a contained
change rather than a sweep.

The tile budget drives the rasterization caps — see MAX_WIDTH below.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import TracebackType

import pymupdf

log = logging.getLogger(__name__)

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


# The cap is a LATENCY budget, not a billing one.
#
# Under meta it was billing: 1,601 tokens per tile + 27 overhead, hard-capped at
# 4 tiles, so pixels past ~1120px were paid for and discarded. Nemotron does not
# bill that way — a 1536x1988 raster costs the SAME ~3,755 prompt tokens as
# 1120x1449. It is still the wrong trade: measured on a 10-page scan, the larger
# raster ran 3.3x slower (111.7s vs 33.7s), lost a page to VISION_TIMEOUT, and
# dropped mean recall 1.000 -> 0.900 as a result. No quality gain was observed
# on any page to offset it.
MAX_WIDTH = 1120

# Portrait pages get the taller box: materially more legible, same cost.
MAX_HEIGHT_PORTRAIT = 1456

# JPEG, never PNG for pages and scans — ~25x the bytes for zero accuracy gain.
JPEG_QUALITY = 85

# Below this fraction of the page area, an embedded image is decoration (a
# logo, a rule) rather than a diagram worth spending a vision call on.
_LARGE_IMAGE_AREA_RATIO = 0.15

# ...but a figure is routinely placed as SEVERAL images, and then no single one
# clears the bar. Measured on a real RFP: a page carrying two stacked aerial
# maps — 27% of the page between them — scored 0.1361 and 0.1358 and took the
# text-only route, so both maps were dropped from the transcription entirely.
# The page above it, with two comparable maps, cleared by 0.0004. That is a
# coin-flip, not a threshold. Combined coverage is the honest question: "how
# much of this page is picture", not "is any one picture big".
#
# Overlapping bboxes are double-counted rather than unioned. The error only
# ever pushes a page TOWARD vision, which is the safe direction — a needless
# vision call costs ~1,600 tokens, a missed diagram costs a requirement.
_COMBINED_IMAGE_AREA_RATIO = 0.20

# Images under this are excluded from the combined sum, so a row of icons or a
# repeated header logo cannot accumulate its way into a vision call.
_DECORATION_AREA_RATIO = 0.02

# A table needs a header and at least one body row to be worth the markup. One
# row is a caption in a box, and rendering it as a table invents a structure.
_TABLE_MIN_ROWS = 2

_PDF_MAGIC = b"%PDF-"
_IMAGE_MAGICS = (
    b"\x89PNG\r\n\x1a\n",  # png
    b"\xff\xd8\xff",  # jpeg
    b"II*\x00",  # tiff, little-endian
    b"MM\x00*",  # tiff, big-endian
)
_ZIP_MAGIC = b"PK\x03\x04"


class UnsupportedDocument(Exception):
    """The bytes are not a PDF or a supported image."""


class EncryptedDocument(Exception):
    """A password-protected PDF. Rejected at the boundary, not mid-page-loop."""


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


def _cell_text(value: str | None) -> str:
    """One table cell as a single line. Newlines inside a cell break the row."""
    return " ".join((value or "").split())


def _normalize_grid(grid: list[list[str]]) -> list[list[str]]:
    """Pad ragged rows, then fold spanned columns back into one.

    ``find_tables`` reports a cell spanning N columns by repeating its value
    into all N, so rfp16's schedule table arrived as
    ``|RFPposted|RFPposted|RFPposted|June 1, 2021|June 1, 2021|June 1, 2021|``.

    Two adjacent columns are folded when every row agrees: the cells are equal,
    or one of them is blank. That second clause is what makes the header line
    up. A spanned *header* cell is reported ONCE, at the column it starts in
    (``["", "ACTION ITEM", "", "", "DATE", ""]``), while the body cells beneath
    it are repeated — so testing whole-column equality keeps both of the body's
    duplicate columns alive and leaves the header one column to the right of its
    own values. Blank-absorbs-value closes that gap.

    Folding *columns* rather than collapsing runs per row is the other half:
    per-row collapse yields rows of differing width, which is the same
    misalignment by a different route.
    """
    if not grid:
        return []
    width = max(len(row) for row in grid)
    grid = [row + [""] * (width - len(row)) for row in grid]

    folded: list[list[str]] = []
    for col in range(width):
        column = [row[col] for row in grid]
        prev = folded[-1] if folded else None
        # Compatible = no row where both cells are non-empty and disagree.
        # Without that guard a blank header would swallow a real value column.
        if prev is not None and all(
            not a or not b or a == b for a, b in zip(prev, column)
        ):
            folded[-1] = [a or b for a, b in zip(prev, column)]
            continue
        folded.append(column)

    folded = [col for col in folded if any(col)]
    return [list(row) for row in zip(*folded)] if folded else []


def _table_markdown(table) -> str:
    """One ``find_tables`` table as a Markdown table, or '' if not worth it."""
    grid = _normalize_grid([[_cell_text(c) for c in row] for row in table.extract()])
    grid = [row for row in grid if any(row)]
    if len(grid) < _TABLE_MIN_ROWS:
        return ""

    header, *body = grid
    if not any(header):
        header = [f"Col{i + 1}" for i in range(len(header))]
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join(["---"] * len(header)) + "|",
    ]
    lines += ["| " + " | ".join(row) + " |" for row in body]
    return "\n".join(lines)


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
        """The embedded text layer for one page ('' for images and scans).

        Deliberately RAW. This feeds the vision-routing threshold
        (``text_layer_min_chars``) and the figure pass's novelty baseline, both
        tuned against exactly these characters — splicing table markup in here
        would move a measured threshold as a side effect of a formatting change.
        The artifact takes :meth:`page_markdown` instead.
        """
        return self._doc[index].get_text()

    def page_markdown(self, index: int) -> str:
        """The text layer with ruled tables rebuilt as Markdown tables.

        ``get_text`` has no table awareness and emits in block order rather than
        visual order, so a ruled table arrived as one cell value per line and
        often in the wrong place on the page — measured on a real RFP, the
        fee-proposal grid landed *below* the signature block that follows it,
        and read as missing entirely.

        Text is clipped to the horizontal bands between the tables rather than
        filtered per block: a block whose bbox is *wider* than the table it
        contains survives an overlap test, and the page then carries every cell
        twice, once in the grid and once as loose prose beneath it.

        Only ruled tables. An unruled tab-stop layout stays linearized —
        ``find_tables``' text strategy does return a grid for one, but splits
        words mid-token ("Cov|erage"), and recovering it properly needs
        x-position clustering that mis-groups indented prose into tables that
        were never there.

        Soft-fails to the raw text layer: a table finder that raises on an odd
        page must degrade to today's output, never fail the page.
        """
        page = self._doc[index]
        try:
            tables = page.find_tables().tables
        except Exception as e:  # pragma: no cover - defensive
            log.warning("page %d table detection failed: %s", index + 1, e)
            return page.get_text()

        rendered = [(pymupdf.Rect(t.bbox), _table_markdown(t)) for t in tables]
        rendered = [(box, md) for box, md in rendered if md]
        if not rendered:
            return page.get_text()
        rendered.sort(key=lambda r: r[0].y0)

        rect = page.rect
        parts: list[str] = []
        cursor = rect.y0
        for box, md in rendered:
            band = pymupdf.Rect(rect.x0, cursor, rect.x1, box.y0)
            if band.height > 1:
                above = page.get_text(clip=band).strip()
                if above:
                    parts.append(above)
            parts.append(md)
            cursor = max(cursor, box.y1)

        tail = page.get_text(clip=pymupdf.Rect(rect.x0, cursor, rect.x1, rect.y1))
        if tail.strip():
            parts.append(tail.strip())
        return "\n\n".join(parts)

    def page_has_large_image(self, index: int) -> bool:
        """True when the page carries enough picture to be worth a vision call.

        Architecture slides carry real constraints — on-prem, specific clouds,
        microservices — that exist *only* as boxes and arrows, so a page with
        both text and a diagram still needs the vision path.

        Two ways to qualify, because one image is not the only way to draw a
        figure: a single image over ``_LARGE_IMAGE_AREA_RATIO``, or several
        non-decorative ones covering ``_COMBINED_IMAGE_AREA_RATIO`` together.
        """
        page = self._doc[index]
        page_area = abs(page.rect.width * page.rect.height)
        if not page_area:
            return False

        combined = 0.0
        for block in page.get_image_info():
            bbox = pymupdf.Rect(block["bbox"])
            ratio = abs(bbox.width * bbox.height) / page_area
            if ratio >= _LARGE_IMAGE_AREA_RATIO:
                return True
            if ratio >= _DECORATION_AREA_RATIO:
                combined += ratio
        return combined >= _COMBINED_IMAGE_AREA_RATIO

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

        ``kind == "slides"`` takes the vector path deliberately: LibreOffice
        emits vector PDF, so scaling a slide up is genuine detail at identical
        token cost. 16:9 slides are landscape and so take ``max_h = MAX_WIDTH``,
        rendering about 1120x630. No new raster constants.
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
