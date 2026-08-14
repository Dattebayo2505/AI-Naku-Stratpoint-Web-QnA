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
