"""Schema, filters, and Jinja rendering for the proposal quote — no browser.

Everything up to the HTML string is exercised here; ``test_pdf_service.py``
picks up from that string. The split is deliberate: these run in milliseconds
and on a machine with no Chromium, so the arithmetic that decides a client's
price is never the part of the suite anyone is tempted to skip.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from stratpoint_rag.agent.contracts import (
    EstimationResult,
    ExtractedRequirements,
    PhaseTimelineItem,
    RoleBreakdownItem,
)
from stratpoint_rag.pdf_gen import filters
from stratpoint_rag.pdf_gen.mapping import EmptyEstimate, build_quote_context, quote_number_for
from stratpoint_rag.pdf_gen.schema import LineItem, MilestoneItem, ProposalQuoteContext
from stratpoint_rag.pdf_gen.templating import render_quote_html

TODAY = date(2026, 8, 9)


def _item(name="Senior Engineer", qty="10", price="75.00", **kw) -> LineItem:
    return LineItem(item_name=name, quantity=Decimal(qty), unit_price=Decimal(price), **kw)


def _context(**overrides) -> ProposalQuoteContext:
    base = dict(
        quote_number="SP-20260809-ABC123",
        quote_date=TODAY,
        valid_until=date(2026, 9, 8),
        line_items=[_item()],
    )
    return ProposalQuoteContext(**{**base, **overrides})


# ── required fields & validation errors ─────────────────────────────────────


def test_a_quote_without_line_items_is_rejected():
    """A $0.00 grand total that looks finished is worse than a failed render."""
    with pytest.raises(ValidationError):
        _context(line_items=[])


@pytest.mark.parametrize("missing", ["quote_number", "quote_date", "valid_until"])
def test_the_identity_fields_are_required(missing):
    base = dict(
        quote_number="SP-1", quote_date=TODAY, valid_until=TODAY, line_items=[_item()]
    )
    base.pop(missing)
    with pytest.raises(ValidationError):
        ProposalQuoteContext(**base)


def test_a_negative_price_is_rejected():
    with pytest.raises(ValidationError):
        _item(price="-1")


def test_a_tax_rate_over_one_hundred_percent_is_rejected():
    with pytest.raises(ValidationError):
        _context(tax_rate_percent=Decimal("101"))


def test_a_missing_client_name_is_not_an_error():
    """Declining to give a name is an offered choice, not a failure."""
    assert _context().client_name == "Prospective Client"


# ── computed money ──────────────────────────────────────────────────────────


def test_the_line_total_is_quantity_times_unit_price():
    assert _item(qty="112.5", price="100").total_amount == Decimal("11250.00")


def test_the_subtotal_is_the_exact_sum_of_the_printed_line_totals():
    """Rounding at each row, not only at the end: the table has to add up on
    paper, which is where a client checks it."""
    ctx = _context(
        line_items=[
            _item(qty="3", price="33.335"),   # 100.01 (half-up, not banker's)
            _item(qty="1", price="0.005"),    # 0.01
        ]
    )
    assert [i.total_amount for i in ctx.line_items] == [Decimal("100.01"), Decimal("0.01")]
    assert ctx.subtotal_amount == Decimal("100.02")


def test_tax_and_grand_total_are_derived():
    ctx = _context(line_items=[_item(qty="1", price="1000")], tax_rate_percent=Decimal("12"))

    assert ctx.tax_amount == Decimal("120.00")
    assert ctx.grand_total_amount == Decimal("1120.00")
    assert ctx.tax_rate_label == "12%"


def test_a_zero_tax_rate_has_no_label_so_the_row_is_hidden():
    """The label is None, not '0.00' — a formatted string is truthy and would
    print a pointless zero-tax line on every quote."""
    assert _context().tax_rate_label is None


def test_the_totals_cannot_be_supplied_by_the_caller():
    """They are computed fields; an LLM-supplied grand total must not stick."""
    ctx = ProposalQuoteContext.model_validate(
        {
            "quote_number": "SP-1",
            "quote_date": TODAY,
            "valid_until": TODAY,
            "line_items": [{"item_name": "x", "quantity": "2", "unit_price": "50"}],
            "grand_total_amount": "999999.00",
        }
    )
    assert ctx.grand_total_amount == Decimal("100.00")


def test_quantities_print_without_trailing_zeros():
    assert _item(qty="60.00").formatted_quantity == "60"
    assert _item(qty="7.50", unit="hrs").formatted_quantity == "7.5 hrs"
    assert _item(qty="100").formatted_quantity == "100"


def test_unit_price_and_total_are_thousands_separated():
    item = _item(qty="1000", price="1234.5")
    assert item.formatted_unit_price == "1,234.50"
    assert item.formatted_total == "1,234,500.00"


# ── filters ─────────────────────────────────────────────────────────────────


def test_currency_format_separates_thousands_and_takes_a_symbol():
    assert filters.currency_format(Decimal("1234567.891"), "$") == "$1,234,567.89"


def test_currency_format_renders_a_missing_amount_as_a_dash():
    """A missing price and a free line item are different facts."""
    assert filters.currency_format(None) == "—"
    assert filters.currency_format("not a number") == "—"


def test_date_format_passes_strings_through_untouched():
    assert filters.date_format(TODAY) == "09 Aug 2026"
    assert filters.date_format("Q3 2026") == "Q3 2026"
    assert filters.date_format(None) == ""


def test_slugify_is_safe_as_one_path_component():
    assert filters.slugify("Northwind Retail, Inc.") == "northwind_retail_inc"
    assert filters.slugify("../../etc/passwd") == "etc_passwd"
    assert filters.slugify(None, fallback="client") == "client"


# ── rendering ───────────────────────────────────────────────────────────────


def test_the_template_renders_every_line_item_and_its_totals():
    html = render_quote_html(
        _context(
            line_items=[_item("Tech Lead", "10", "100"), _item("QA", "20", "50")],
            tax_rate_percent=Decimal("12"),
        )
    )

    assert "Tech Lead" in html and "QA" in html
    assert "$1,000.00" in html and "$1,000.00" in html  # both line totals
    assert "$2,000.00" in html                          # subtotal
    assert "$240.00" in html                            # tax
    assert "$2,240.00" in html                          # grand total


def test_the_tax_row_is_absent_at_a_zero_rate():
    assert "Sales Tax" not in render_quote_html(_context())


def test_the_template_renders_both_pages_and_the_break_between_them():
    html = render_quote_html(_context())
    assert 'class="page-sheet page-1"' in html
    assert 'class="page-sheet page-2"' in html
    assert 'class="page-break"' in html


def test_document_derived_text_is_escaped():
    """A brief is attacker-controllable and the output is handed to a browser
    engine. Autoescape is the thing standing between the two."""
    html = render_quote_html(
        _context(
            project_title="Loyalty App",
            project_description="<script>alert('xss')</script> & more",
        )
    )

    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html and "&amp; more" in html


def test_an_unknown_template_variable_raises_rather_than_rendering_blank():
    """StrictUndefined: a typo'd key must not produce a professional-looking
    quote with a blank column."""
    from jinja2 import UndefinedError

    from stratpoint_rag.pdf_gen.templating import get_environment

    template = get_environment().from_string("{{ nope }}")
    with pytest.raises(UndefinedError):
        template.render()


def test_render_validates_a_raw_dict_before_it_reaches_the_template():
    with pytest.raises(ValidationError):
        render_quote_html({"quote_number": "SP-1"})


# ── mapping from the agent contracts ────────────────────────────────────────


def _estimation() -> EstimationResult:
    return EstimationResult(
        total_cost_usd=20_000.0,
        estimated_weeks=10.0,
        role_breakdown=[
            RoleBreakdownItem(
                role="Tech Lead", estimated_hours=100.0, hourly_rate=100.0, total_cost=10_000.0
            ),
            RoleBreakdownItem(
                role="QA", estimated_hours=200.0, hourly_rate=50.0, total_cost=10_000.0
            ),
        ],
        phase_timeline=[
            PhaseTimelineItem(
                phase_name="Phase 1: Discovery & System Architecture",
                duration_weeks=2.0,
                milestones=["Architecture Document", "Wireframes"],
            ),
            PhaseTimelineItem(
                phase_name="Phase 2: Core Development", duration_weeks=8.0, milestones=["Build"]
            ),
        ],
        summary="10 weeks, $20,000.",
    )


def test_the_mapping_turns_roles_into_priced_rows():
    ctx = build_quote_context(proposal_id="abc123def", estimation=_estimation(), today=TODAY)

    assert [i.item_name for i in ctx.line_items] == ["Tech Lead", "QA"]
    assert ctx.subtotal_amount == Decimal("20000.00")
    assert ctx.quote_number == "SP-20260809-ABC123"


def test_the_mapping_refuses_to_quote_an_empty_estimate():
    with pytest.raises(EmptyEstimate):
        build_quote_context(proposal_id="abc123", estimation=None, today=TODAY)


def test_a_total_without_a_role_breakdown_becomes_one_fixed_scope_row():
    estimation = EstimationResult(
        total_cost_usd=5000.0, estimated_weeks=4.0, summary="Fixed scope."
    )

    ctx = build_quote_context(proposal_id="abc123", estimation=estimation, today=TODAY)

    assert len(ctx.line_items) == 1
    assert ctx.grand_total_amount == Decimal("5000.00")


def test_phase_dates_chain_from_the_quote_date():
    ctx = build_quote_context(proposal_id="abc123", estimation=_estimation(), today=TODAY)

    assert [m.phase_number for m in ctx.milestones] == [1, 2]
    # Phase 1 runs 2 weeks from 09 Aug; phase 2 starts where it ends.
    assert ctx.milestones[0].date_range == "09 Aug – 23 Aug 2026"
    assert ctx.milestones[1].date_range == "23 Aug – 18 Oct 2026"


def test_the_chevron_label_drops_the_phase_prefix_the_template_re_adds():
    ctx = build_quote_context(proposal_id="abc123", estimation=_estimation(), today=TODAY)

    # Truncated at a word boundary: a mid-word cut reads as a rendering bug.
    assert ctx.milestones[0].phase_name == "Discovery & System…"
    assert ctx.milestones[1].phase_name == "Core Development"


def test_unread_pages_of_the_brief_travel_with_the_price():
    """A quote built on a brief where vision choked on 6 of 20 pages must not
    read like one built on a clean brief."""
    requirements = ExtractedRequirements(
        features=["SSO"], pages_total=20, pages_parsed=14, pages_failed=[3, 4, 5, 6, 7, 8]
    )

    ctx = build_quote_context(
        proposal_id="abc123",
        estimation=_estimation(),
        requirements=requirements,
        today=TODAY,
    )

    assert "3, 4, 5, 6, 7, 8" in ctx.notes
    assert "could not be read" in ctx.notes
    # Clarifications & notes removed from template per styling requirements
    assert "could not be read" not in render_quote_html(ctx)
    assert "Clarifications &amp; Notes" not in render_quote_html(ctx)
    assert "Payment Schedule" not in render_quote_html(ctx)


def test_the_mapping_invents_no_client_name():
    ctx = build_quote_context(proposal_id="abc123", estimation=_estimation(), today=TODAY)

    assert ctx.client_name == "Prospective Client"
    assert "acme" not in render_quote_html(ctx).lower()


def test_a_visitor_supplied_name_reaches_the_document():
    ctx = build_quote_context(
        proposal_id="abc123",
        estimation=_estimation(),
        client_name="Northwind Retail",
        project_name="Loyalty App",
        today=TODAY,
    )

    html = render_quote_html(ctx)
    assert "Northwind Retail" in html and "Loyalty App" in html


def test_requirements_carrying_dropped_keys_do_not_break_the_mapping():
    """The old contract had client_name/project_name; an LLM path may still
    emit them. The schema is the filter, not the caller."""
    ctx = build_quote_context(
        proposal_id="abc123",
        estimation=_estimation(),
        requirements={"features": ["SSO"], "client_name": "Acme Innovations"},
        today=TODAY,
    )

    assert "Acme" not in render_quote_html(ctx)


def test_the_quote_number_is_derived_not_counted():
    """Two uvicorn workers must not hand out the same one."""
    assert quote_number_for("deadbeefcafe", TODAY) == "SP-20260809-DEADBE"


def test_a_milestone_label_too_long_for_the_chevron_is_rejected():
    with pytest.raises(ValidationError):
        MilestoneItem(phase_number=1, phase_name="x" * 41, title="t")


def test_currency_detection_usd_and_php():
    from stratpoint_rag.docparse.extract import detect_currency

    assert detect_currency("Budget is 500,000 USD") == ("$", "USD")
    assert detect_currency("Target price: $10,000") == ("$", "USD")
    assert detect_currency("Target budget: ₱250,000 PHP") == ("₱", "PHP")
    assert detect_currency("Project budget is in pesos (PhP 100,000)") == ("₱", "PHP")
    assert detect_currency("") == ("$", "USD")


def test_quote_context_currency_pesos():
    requirements = ExtractedRequirements(
        features=["Mobile App"],
        constraints=["Budget: 500,000 PHP"],
    )
    ctx = build_quote_context(
        proposal_id="abc123",
        estimation=_estimation(),
        requirements=requirements,
        today=TODAY,
    )

    assert ctx.currency_symbol == "₱"
    assert ctx.currency_code == "PHP"
    html = render_quote_html(ctx)
    assert "₱" in html
    assert "PHP" in html
