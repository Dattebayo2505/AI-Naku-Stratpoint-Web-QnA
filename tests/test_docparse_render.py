"""docparse.render — validation, text-layer reads, and rasterization.

All fixtures are *generated* with PyMuPDF rather than committed as binaries:
deterministic, tiny, and the construction itself documents what "has a text
layer" versus "image-only" actually means.
"""

from __future__ import annotations

import struct

import pytest

from stratpoint_rag.docparse import render


# ── fixtures ────────────────────────────────────────────────────────────────

A4_W, A4_H = 595, 842  # points, portrait


@pytest.fixture
def text_pdf(tmp_path):
    """A 2-page A4 PDF with a real embedded text layer."""
    import pymupdf

    doc = pymupdf.open()
    for n in (1, 2):
        page = doc.new_page(width=A4_W, height=A4_H)
        page.insert_text((72, 100), f"Page {n} heading", fontsize=18)
        page.insert_text(
            (72, 140),
            f"Body text for page {n}. " * 12,
            fontsize=11,
        )
    path = tmp_path / "text.pdf"
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def image_pdf(tmp_path, text_pdf):
    """The same content re-wrapped as a pixmap — no text layer at all."""
    import pymupdf

    src = pymupdf.open(text_pdf)
    out = pymupdf.open()
    for page in src:
        pix = page.get_pixmap(dpi=72)
        new = out.new_page(width=pix.width, height=pix.height)
        new.insert_image(pymupdf.Rect(0, 0, pix.width, pix.height), pixmap=pix)
    path = tmp_path / "scan.pdf"
    out.save(path)
    src.close()
    out.close()
    return path


@pytest.fixture
def landscape_pdf(tmp_path):
    import pymupdf

    doc = pymupdf.open()
    doc.new_page(width=A4_H, height=A4_W)
    path = tmp_path / "landscape.pdf"
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def png_image(tmp_path):
    """A bare 2000x1000 PNG — larger than the tile cap, so it must downscale."""
    import pymupdf

    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 2000, 1000))
    pix.clear_with(200)
    path = tmp_path / "diagram.png"
    pix.save(path)
    return path


@pytest.fixture
def encrypted_pdf(tmp_path):
    import pymupdf

    doc = pymupdf.open()
    doc.new_page(width=A4_W, height=A4_H)
    path = tmp_path / "locked.pdf"
    doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret")
    doc.close()
    return path


def _jpeg_dims(data: bytes) -> tuple[int, int]:
    """Parse WxH out of a JPEG's SOF marker, so the test needs no image lib."""
    i = 2
    while i < len(data):
        assert data[i] == 0xFF, "not a valid JPEG segment stream"
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3):
            h, w = struct.unpack(">HH", data[i + 5 : i + 9])
            return w, h
        i += 2 + struct.unpack(">H", data[i + 2 : i + 4])[0]
    raise AssertionError("no SOF marker found")


# ── magic-byte sniffing ─────────────────────────────────────────────────────
#
# st.file_uploader's `type=` is a client-side filter, not a security boundary,
# and /upload is reachable without Streamlit.


@pytest.mark.parametrize(
    "head, expected",
    [
        (b"%PDF-1.7\n...", "pdf"),
        (b"\x89PNG\r\n\x1a\n", "image"),
        (b"\xff\xd8\xff\xe0", "image"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image"),
        (b"II*\x00", "image"),
        (b"MM\x00*", "image"),
    ],
)
def test_sniff_kind_recognises_supported_formats(head, expected):
    assert render.sniff_kind(head) == expected


@pytest.mark.parametrize(
    "head",
    [
        b"PK\x03\x04",  # .pptx / .docx — a zip container
        b"\xd0\xcf\x11\xe0",  # legacy .doc / .ppt
        b"GIF89a",
        b"",
        b"%PD",  # truncated
    ],
)
def test_sniff_kind_rejects_unsupported_formats(head):
    assert render.sniff_kind(head) is None


def test_pptx_is_rejected_despite_its_extension(tmp_path):
    """Content decides, not the filename."""
    path = tmp_path / "deck.pdf"
    path.write_bytes(b"PK\x03\x04" + b"\x00" * 100)
    with pytest.raises(render.UnsupportedDocument):
        render.open_document(path)


# ── opening ─────────────────────────────────────────────────────────────────


def test_open_pdf_reports_kind_and_page_count(text_pdf):
    with render.open_document(text_pdf) as doc:
        assert doc.kind == "pdf"
        assert doc.page_count == 2


def test_open_image_reports_one_page(png_image):
    with render.open_document(png_image) as doc:
        assert doc.kind == "image"
        assert doc.page_count == 1


def test_encrypted_pdf_is_rejected_at_open(encrypted_pdf):
    """One line at the boundary, versus a confusing failure deep in the page loop."""
    with pytest.raises(render.EncryptedDocument):
        render.open_document(encrypted_pdf)


def test_missing_file_raises_unsupported(tmp_path):
    with pytest.raises(render.UnsupportedDocument):
        render.open_document(tmp_path / "nope.pdf")


# ── text layer ──────────────────────────────────────────────────────────────


def test_text_layer_is_read_from_a_digital_pdf(text_pdf):
    with render.open_document(text_pdf) as doc:
        assert "Page 1 heading" in doc.page_text(0)
        assert "Page 2 heading" in doc.page_text(1)


def test_image_only_pdf_has_no_text_layer(image_pdf):
    with render.open_document(image_pdf) as doc:
        assert doc.page_text(0).strip() == ""


def test_bare_image_has_no_text_layer(png_image):
    with render.open_document(png_image) as doc:
        assert doc.page_text(0).strip() == ""


def test_page_carries_large_image_detects_the_scan(image_pdf):
    """Diagrams hold requirements the text layer misses — force vision on them."""
    with render.open_document(image_pdf) as doc:
        assert doc.page_has_large_image(0) is True


def test_page_carries_large_image_is_false_for_pure_text(text_pdf):
    with render.open_document(text_pdf) as doc:
        assert doc.page_has_large_image(0) is False


# ── rasterization: the tile budget ──────────────────────────────────────────
#
# The constraint is TILES, not bytes: 1,601 tokens per tile + 27 overhead, hard
# capped at 4 tiles. Everything above ~1120px is silently discarded, and
# measurably *hurt* transcription in the endpoint probe.


def test_portrait_page_renders_within_the_portrait_cap(text_pdf):
    with render.open_document(text_pdf) as doc:
        w, h = _jpeg_dims(doc.rasterize(0))
    assert w <= render.MAX_WIDTH
    assert h <= render.MAX_HEIGHT_PORTRAIT


def test_portrait_page_uses_the_full_tile_budget(text_pdf):
    """A4 is 595x842pt; rendering at 1:1 wastes legibility at identical token cost.

    A4's aspect ratio (1.415) is taller than the 1120x1456 box (1.3), so height
    is the binding dimension and the result is 1029x1456, not 1120-wide.
    """
    with render.open_document(text_pdf) as doc:
        w, h = _jpeg_dims(doc.rasterize(0))
    assert h == render.MAX_HEIGHT_PORTRAIT
    assert w == pytest.approx(round(A4_W * render.MAX_HEIGHT_PORTRAIT / A4_H), abs=2)


def test_landscape_page_renders_within_the_square_cap(landscape_pdf):
    with render.open_document(landscape_pdf) as doc:
        w, h = _jpeg_dims(doc.rasterize(0))
    assert w <= render.MAX_WIDTH
    assert h <= render.MAX_WIDTH


def test_aspect_ratio_is_preserved(text_pdf):
    with render.open_document(text_pdf) as doc:
        w, h = _jpeg_dims(doc.rasterize(0))
    assert h / w == pytest.approx(A4_H / A4_W, rel=0.01)


def test_oversized_bare_image_is_downscaled(png_image):
    with render.open_document(png_image) as doc:
        w, h = _jpeg_dims(doc.rasterize(0))
    assert (w, h) == (render.MAX_WIDTH, render.MAX_WIDTH // 2)


def test_bare_image_is_never_upscaled(tmp_path):
    """Upscaling a raster adds no information — only render cost."""
    import pymupdf

    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 300))
    pix.clear_with(128)
    small = tmp_path / "small.png"
    pix.save(small)

    with render.open_document(small) as doc:
        w, h = _jpeg_dims(doc.rasterize(0))
    assert (w, h) == (400, 300)


def test_rasterize_emits_jpeg_not_png(text_pdf):
    """PNG is ~25x larger for scans with zero accuracy gain."""
    with render.open_document(text_pdf) as doc:
        data = doc.rasterize(0)
    assert data[:3] == b"\xff\xd8\xff"


def test_rasterize_rejects_an_out_of_range_page(text_pdf):
    with render.open_document(text_pdf) as doc:
        with pytest.raises(IndexError):
            doc.rasterize(5)


# ── combined image coverage ─────────────────────────────────────────────────
#
# Regression: a real RFP page carrying two stacked aerial maps took the
# text-only route and lost both. Neither map cleared the per-image 0.15 bar
# (0.1361 and 0.1358) though together they covered 27% of the page, while the
# page after it cleared by 0.0004. The single-image test was a coin flip.


@pytest.fixture
def two_figure_pdf(tmp_path):
    """One A4 page, two images at ~13% of page area each — 26% combined.

    Deliberately straddles the thresholds: under _LARGE_IMAGE_AREA_RATIO
    individually, over _COMBINED_IMAGE_AREA_RATIO together.
    """
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=A4_W, height=A4_H)
    page.insert_text((72, 60), "Site Information", fontsize=14)

    box_w, box_h = 440, 148  # 440*148 / (595*842) = 0.130 of the page
    # The pixmap's aspect must match the target rect: insert_image preserves
    # proportion, so a mismatched source silently lands smaller than the rect
    # and the fixture stops testing what it says it tests.
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, box_w * 2, box_h * 2))
    pix.clear_with(180)
    page.insert_image(pymupdf.Rect(72, 100, 72 + box_w, 100 + box_h), pixmap=pix)
    page.insert_image(pymupdf.Rect(72, 300, 72 + box_w, 300 + box_h), pixmap=pix)

    path = tmp_path / "two_figures.pdf"
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def icon_row_pdf(tmp_path):
    """Twenty-four small icons — ~1% of the page each, ~23% combined.

    Deliberately sums to MORE than _COMBINED_IMAGE_AREA_RATIO, so the only
    thing standing between this page and a wasted vision call is the
    per-image decoration floor. A fixture that stayed under the combined bar
    would pass whether or not that floor existed.
    """
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=A4_W, height=A4_H)
    page.insert_text((72, 60), "Body text " * 30, fontsize=11)

    side = 70  # 70*70 / (595*842) = 0.0098 each, 0.235 for twenty-four
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, side, side))
    pix.clear_with(90)
    for i in range(24):
        x, y = 72 + (i % 6) * 80, 150 + (i // 6) * 80
        page.insert_image(pymupdf.Rect(x, y, x + side, y + side), pixmap=pix)

    path = tmp_path / "icons.pdf"
    doc.save(path)
    doc.close()
    return path


def _image_ratios(doc, index=0):
    """Each embedded image's share of the page, as page_has_large_image sees it."""
    import pymupdf

    page = doc._doc[index]
    area = abs(page.rect.width * page.rect.height)
    out = []
    for b in page.get_image_info():
        r = pymupdf.Rect(b["bbox"])
        out.append(abs(r.width * r.height) / area)
    return out


def test_two_medium_figures_together_earn_a_vision_call(two_figure_pdf):
    """The reported bug: a figure split across two images was routed to text."""
    with render.open_document(two_figure_pdf) as doc:
        ratios = _image_ratios(doc)
        # The premise of the regression — without it this test could pass for
        # the wrong reason, e.g. if the fixture's images landed oversized.
        assert len(ratios) == 2, ratios
        assert all(r < render._LARGE_IMAGE_AREA_RATIO for r in ratios), ratios
        assert sum(ratios) >= render._COMBINED_IMAGE_AREA_RATIO, ratios

        assert doc.page_has_large_image(0) is True


def test_a_row_of_icons_does_not_accumulate_into_a_vision_call(icon_row_pdf):
    """Decoration must not sum its way past the bar — that is the cost guard."""
    with render.open_document(icon_row_pdf) as doc:
        ratios = _image_ratios(doc)
        assert len(ratios) == 24, ratios
        # Individually decoration, but they out-total the combined bar; only the
        # per-image floor keeps them from buying a vision call.
        assert all(r < render._DECORATION_AREA_RATIO for r in ratios), ratios
        assert sum(ratios) > render._COMBINED_IMAGE_AREA_RATIO, sum(ratios)

        assert doc.page_has_large_image(0) is False
