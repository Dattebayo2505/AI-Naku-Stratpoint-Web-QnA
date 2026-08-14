"""Real Chromium renders: page count, PDF header, offline behaviour, concurrency.

These are the only tests in the suite that need a browser. They skip rather
than fail when ``playwright install chromium`` has not been run, so a
contributor who has not done the one-time download still gets a green suite —
but they are NOT marked ``integration``, because nothing here touches the
network and they are the only proof that the template's two-page layout
survives the print engine. Skipping silently is the failure mode to avoid, so
the skip reason names the fix.

PyMuPDF reads the results back; it is already a dependency (docparse renders
with it) and is the only page-count check that does not trust our own writer.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal

import pytest

from stratpoint_rag.pdf_gen import PdfOptions, PdfRenderError, generate_pdf_from_html
from stratpoint_rag.pdf_gen.pdf_service import agenerate_pdf_from_html
from stratpoint_rag.pdf_gen.schema import LineItem, ProposalQuoteContext
from stratpoint_rag.pdf_gen.templating import render_quote_html

fitz = pytest.importorskip("fitz")

TODAY = date(2026, 8, 9)


@pytest.fixture(scope="module", autouse=True)
def _require_chromium():
    """Skip the module — with the fix in the message — if no browser is present."""
    from playwright.sync_api import Error, sync_playwright

    from stratpoint_rag.pdf_gen import config

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=config.browser_args())
            browser.close()
    except Error as ex:
        pytest.skip(f"chromium unavailable — run 'uv run playwright install chromium' ({ex})")


def _quote_html(**overrides) -> str:
    base = dict(
        quote_number="SP-20260809-ABC123",
        quote_date=TODAY,
        valid_until=date(2026, 9, 8),
        client_name="Northwind Retail",
        project_title="Loyalty App",
        line_items=[
            LineItem(item_name="Tech Lead", quantity=Decimal("100"), unit="hrs",
                     unit_price=Decimal("100")),
            LineItem(item_name="QA", quantity=Decimal("200"), unit="hrs",
                     unit_price=Decimal("50")),
        ],
    )
    return render_quote_html(ProposalQuoteContext(**{**base, **overrides}))


def _read(path):
    return fitz.open(str(path))


# ── the deliverable ─────────────────────────────────────────────────────────


def test_the_quote_prints_as_a_two_page_a4_pdf(tmp_path):
    """The layout's whole premise: page 1 is cost & scope, page 2 is roadmap
    & terms. A third page means the @page margins were double-applied."""
    out = generate_pdf_from_html(_quote_html(), tmp_path / "quote.pdf")

    assert out.read_bytes().startswith(b"%PDF-1.")
    assert out.stat().st_size > 1024

    with _read(out) as doc:
        assert doc.page_count == 2
        # A4 portrait at 72dpi is 595x842pt. Chromium rounds; ±2pt is slack for
        # that, not for a different paper size.
        rect = doc[0].rect
        assert abs(rect.width - 595) < 2 and abs(rect.height - 842) < 2


def test_page_one_carries_the_costs_and_page_two_the_roadmap(tmp_path):
    out = generate_pdf_from_html(_quote_html(), tmp_path / "quote.pdf")

    with _read(out) as doc:
        # Lowercased: the section headings carry `text-transform: uppercase`,
        # and Chromium bakes that into the glyphs it writes to the PDF.
        page1, page2 = doc[0].get_text().lower(), doc[1].get_text().lower()

    assert "cost & deliverable schedule" in page1
    assert "$20,000.00" in page1 and "tech lead" in page1
    assert "execution phases" in page2 and "page 2 of 2" in page2


def test_the_client_name_reaches_the_printed_page(tmp_path):
    out = generate_pdf_from_html(_quote_html(), tmp_path / "quote.pdf")

    with _read(out) as doc:
        assert "Northwind Retail" in doc[0].get_text()


def test_backgrounds_are_printed(tmp_path):
    """print_background=False silently drops the dark header banner and every
    table header fill — the document still renders, just as a plain white page."""
    out = generate_pdf_from_html(_quote_html(), tmp_path / "quote.pdf")

    with _read(out) as doc:
        # The banner is a filled rect on page 1; without backgrounds there are
        # no drawings at all.
        assert doc[0].get_drawings()


# ── offline behaviour ───────────────────────────────────────────────────────


def test_an_external_asset_is_blocked_rather_than_stalling_the_render(tmp_path):
    """A CDN font on a container with no egress does not error, it hangs. The
    route guard turns that into a fast render with a fallback font."""
    html = (
        '<html><head><link rel="stylesheet" '
        'href="https://fonts.example.invalid/x.css"></head>'
        "<body><h1>Offline</h1></body></html>"
    )

    out = generate_pdf_from_html(
        html, tmp_path / "offline.pdf", PdfOptions(timeout_ms=10_000)
    )

    with _read(out) as doc:
        assert "Offline" in doc[0].get_text()


def test_a_data_uri_image_survives_the_block(tmp_path):
    """Inlined assets are the supported route, so the guard must not eat them."""
    from stratpoint_rag.pdf_gen.assets import data_uri

    svg = tmp_path / "mark.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40">'
        '<rect width="40" height="40" fill="#0284c7"/></svg>',
        encoding="utf-8",
    )
    html = f'<html><body><img src="{data_uri(svg)}"></body></html>'

    out = generate_pdf_from_html(html, tmp_path / "logo.pdf")

    # An inlined SVG lands as vector drawings, not as an embedded raster, so
    # get_drawings() is the check — get_images() is empty for a rendered SVG
    # even when it is plainly on the page.
    with _read(out) as doc:
        assert doc[0].get_drawings()


def test_a_local_asset_resolves_when_a_base_dir_is_given(tmp_path):
    (tmp_path / "mark.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40">'
        '<rect width="40" height="40" fill="#0d9488"/></svg>',
        encoding="utf-8",
    )
    html = '<html><body><img src="mark.svg"></body></html>'

    out = generate_pdf_from_html(
        html, tmp_path / "rel.pdf", PdfOptions(base_dir=tmp_path)
    )

    with _read(out) as doc:
        assert doc[0].get_drawings()
    # The temp document is cleaned up, not left beside the caller's assets.
    assert not list(tmp_path.glob(".render-*.html"))


# ── failure & concurrency ───────────────────────────────────────────────────


def test_a_bad_launch_flag_raises_a_typed_error(tmp_path, monkeypatch):
    """Playwright's own Error must not escape into a /chat turn as-is."""
    from stratpoint_rag.pdf_gen import config

    monkeypatch.setattr(config, "chromium_executable", lambda: "/nonexistent/chrome")

    with pytest.raises(PdfRenderError):
        generate_pdf_from_html("<html><body>x</body></html>", tmp_path / "x.pdf")


def test_concurrent_renders_all_complete(tmp_path):
    """Four simultaneous requests against a semaphore sized for 2. The point is
    that the excess queues rather than failing or interleaving into one file."""
    html = _quote_html()

    with ThreadPoolExecutor(max_workers=4) as pool:
        outs = list(
            pool.map(
                lambda i: generate_pdf_from_html(html, tmp_path / f"q{i}.pdf"),
                range(4),
            )
        )

    assert len(outs) == 4
    for out in outs:
        assert out.read_bytes().startswith(b"%PDF-1.")
        with _read(out) as doc:
            assert doc.page_count == 2


async def test_the_async_renderer_produces_the_same_document(tmp_path):
    """Callers already inside an event loop cannot use the sync API at all —
    it raises rather than degrading — so this is a separate implementation."""
    out = await agenerate_pdf_from_html(_quote_html(), tmp_path / "async.pdf")

    with _read(out) as doc:
        assert doc.page_count == 2
        assert re.search(r"SP-20260809-ABC123", doc[0].get_text())


def test_proposal_pdf_render_with_capped_phases():
    from stratpoint_rag.agent.contracts import EstimationResult, PhaseTimelineItem, RoleBreakdownItem
    from stratpoint_rag.pdf_gen import build_quote_context, render_quote_html, generate_pdf_from_html
    import tempfile
    from pathlib import Path

    phases = [
        PhaseTimelineItem(phase_name=f"Phase {i}: Core Milestone Implementation", duration_weeks=2.0, milestones=["Deliverable A", "Deliverable B"])
        for i in range(1, 10)  # 9 phases (Hard limit)
    ]
    est = EstimationResult(
        total_cost_usd=15000.0,
        currency_code="USD",
        estimated_weeks=18.0,
        role_breakdown=[RoleBreakdownItem(role="Tech Lead", estimated_hours=100, hourly_rate=70, total_cost=7000)],
        phase_timeline=phases,
        summary="Capped phase test proposal"
    )
    context = build_quote_context(
        proposal_id="test001",
        estimation=est,
        client_name="Acme Corp",
        project_name="Enterprise Platform"
    )
    html = render_quote_html(context)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        out_path = Path(tmp.name)
    generate_pdf_from_html(html, out_path)
    assert out_path.exists()
    assert out_path.stat().st_size > 5000
    out_path.unlink()

