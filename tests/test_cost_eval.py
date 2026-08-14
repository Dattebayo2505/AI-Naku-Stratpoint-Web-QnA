"""cost_eval — do the numbers on a generated quote actually add up?

The schema's arithmetic is already unit-tested in test_quote_template.py. What
this layer adds is scoring the quotes the pipeline really produced, including
the one failure those unit tests structurally cannot see: an estimation
denominated in pesos rendered under a dollar sign, which is a 60x error in the
only number the client reads.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from stratpoint_rag.evaluation import cost_eval as ce
from stratpoint_rag.pdf_gen.schema import LineItem, ProposalQuoteContext


def _ctx(**overrides) -> ProposalQuoteContext:
    kwargs = dict(
        quote_number="Q-1",
        quote_date=date(2026, 8, 14),
        valid_until=date(2026, 9, 14),
        currency_symbol="$",
        currency_code="USD",
        line_items=[
            LineItem(item_name="Senior Dev", quantity=Decimal("60"),
                     unit_price=Decimal("100.00")),
            LineItem(item_name="QA", quantity=Decimal("20"),
                     unit_price=Decimal("50.00")),
        ],
        tax_rate_percent=Decimal("12"),
    )
    kwargs.update(overrides)
    return ProposalQuoteContext(**kwargs)


def test_a_coherent_quote_passes_every_check():
    ok, reasons = ce.score_quote(_ctx(), declared_currency="USD")

    assert ok
    assert reasons == []


def test_the_subtotal_must_equal_the_sum_of_the_line_totals():
    ctx = _ctx()

    # 60*100 + 20*50 = 7000.00 — the number the table prints beneath the rows.
    assert ctx.subtotal_amount == Decimal("7000.00")


def test_tax_must_equal_the_subtotal_times_the_rate():
    ctx = _ctx()

    assert ctx.tax_amount == Decimal("840.00")
    assert ctx.grand_total_amount == Decimal("7840.00")


def test_a_peso_estimation_rendered_under_a_dollar_sign_fails():
    """The documented 60x bug: amounts priced in PHP, printed as USD."""
    ctx = _ctx(currency_code="USD", currency_symbol="$")

    ok, reasons = ce.score_quote(ctx, declared_currency="PHP")

    assert not ok
    assert any("PHP" in r for r in reasons)


def test_a_symbol_that_disagrees_with_its_own_currency_code_fails():
    ctx = _ctx(currency_code="USD", currency_symbol="₱")

    ok, reasons = ce.score_quote(ctx, declared_currency="USD")

    assert not ok
    assert any("symbol" in r.lower() for r in reasons)


def test_an_undeclared_currency_is_not_a_failure():
    """EstimationResult.currency_code defaults to None meaning *undeclared*.

    A re-supplied estimation dict copied out of a prior Observation predates the
    field, so it arrives without one. None means undeclared, not USD — failing
    it would score the capture-sink path as a currency bug.
    """
    ok, reasons = ce.score_quote(_ctx(), declared_currency=None)

    assert ok, reasons


def test_run_aggregates_quotes_and_names_the_failing_brief():
    good = {"file": "rfp-good.pdf", "context": _ctx(), "declared_currency": "USD"}
    bad = {"file": "rfp-bad.pdf", "context": _ctx(currency_symbol="₱"),
           "declared_currency": "USD"}

    res = ce.run_cost_eval([good, bad])

    assert res["total"] == 2
    assert res["passed"] == 1
    assert res["pass_rate"] == pytest.approx(0.5)
    assert res["failures"][0]["file"] == "rfp-bad.pdf"


def test_the_layer_skips_when_nothing_has_been_seeded():
    res = ce.run_cost_eval([])

    assert res["total"] == 0
    assert res["pass_rate"] == 0.0


def test_a_quote_that_could_not_be_built_counts_as_a_failure():
    """An estimation with no priced roles raises EmptyEstimate in mapping.

    It must be scored, not skipped and not allowed to escape: skipping hides a
    real defect (a proposal with nothing behind its price), and letting the
    exception propagate takes down the whole eval command with it, losing every
    other layer's result to one malformed case.
    """
    good = {"file": "ok.pdf", "context": _ctx(), "declared_currency": "USD"}
    broken = {"file": "empty-estimate.pdf", "context": None,
              "declared_currency": "USD", "error": "EmptyEstimate: no priced work"}

    res = ce.run_cost_eval([good, broken])

    assert res["total"] == 2
    assert res["passed"] == 1
    assert res["failures"][0]["file"] == "empty-estimate.pdf"
    assert "EmptyEstimate" in res["failures"][0]["reasons"][0]
