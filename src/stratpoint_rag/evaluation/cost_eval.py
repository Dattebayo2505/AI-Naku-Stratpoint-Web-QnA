"""Cost eval (Component #13) — do the numbers on a generated quote add up?

`test_quote_template.py` already proves the schema's arithmetic in isolation.
This layer scores the quotes the pipeline actually produced, which catches one
class those unit tests structurally cannot reach: an estimation denominated in
one currency rendered under another's symbol. The schema is perfectly
self-consistent in that case — every total is the correct sum of its addends —
and the quote is still wrong by the FX rate, roughly 60x for PHP against USD.

A quote is the unit: it passes when all four checks hold.

Scores seeded cases, never a re-run of the estimator.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any, Iterable

from stratpoint_rag.pdf_gen.schema import ProposalQuoteContext, money

if TYPE_CHECKING:
    from stratpoint_rag.evaluation.harness import LayerResult

# The two currencies this product prices in. A symbol outside this map is not
# judged — inventing a mapping for it would fail correct behaviour.
SYMBOL_FOR_CODE = {"USD": "$", "PHP": "₱"}


def score_quote(
    context: ProposalQuoteContext, declared_currency: str | None
) -> tuple[bool, list[str]]:
    """Check one quote's arithmetic and currency. Returns (ok, reasons)."""
    reasons: list[str] = []

    line_sum = money(sum((i.total_amount for i in context.line_items), Decimal("0")))
    if line_sum != context.subtotal_amount:
        reasons.append(
            f"line totals sum to {line_sum} but the subtotal prints "
            f"{context.subtotal_amount}"
        )

    expected_tax = money(context.subtotal_amount * context.tax_rate_percent / Decimal("100"))
    if expected_tax != context.tax_amount:
        reasons.append(
            f"tax at {context.tax_rate_percent}% should be {expected_tax}, "
            f"quote prints {context.tax_amount}"
        )

    expected_total = money(context.subtotal_amount + context.tax_amount)
    if expected_total != context.grand_total_amount:
        reasons.append(
            f"grand total should be {expected_total}, quote prints "
            f"{context.grand_total_amount}"
        )

    # The relabelling check. `declared_currency` is None when the estimation did
    # not carry one — that means *undeclared*, not USD, so there is nothing to
    # contradict and nothing to fail. Failing it would score the capture-sink
    # re-supply path (where the dict predates the field) as a currency bug.
    if declared_currency and declared_currency != context.currency_code:
        reasons.append(
            f"estimation is denominated in {declared_currency} but the quote "
            f"renders {context.currency_code}"
        )

    expected_symbol = SYMBOL_FOR_CODE.get(context.currency_code)
    if expected_symbol and context.currency_symbol != expected_symbol:
        reasons.append(
            f"symbol {context.currency_symbol!r} disagrees with currency code "
            f"{context.currency_code}"
        )

    return not reasons, reasons


def run_cost_eval(cases: Iterable[dict[str, Any]] | None = None) -> dict:
    """Score seeded quotes. Each case carries a context and its declared currency."""
    if cases is None:
        from stratpoint_rag.evaluation.seed_cases import load_quote_cases

        cases = load_quote_cases()
    cases = list(cases)

    passed = 0
    failures: list[dict[str, Any]] = []
    for case in cases:
        if case.get("context") is None:
            # The quote could not be assembled at all (see seed_cases). That is
            # a failed quote, not an absent one — skipping it would drop a
            # proposal with nothing behind its price out of the denominator.
            failures.append({
                "file": case.get("file"),
                "reasons": [case.get("error") or "quote could not be built"],
            })
            continue
        ok, reasons = score_quote(case["context"], case.get("declared_currency"))
        if ok:
            passed += 1
        else:
            failures.append({"file": case.get("file"), "reasons": reasons})

    total = len(cases)
    return {
        "total": total,
        "passed": passed,
        "pass_rate": (passed / total) if total else 0.0,
        "failures": failures,
    }


def layer() -> LayerResult:
    # Deferred import: harness imports this module to build REGISTRY.
    from stratpoint_rag.evaluation.harness import LayerResult

    res = run_cost_eval()
    if res["total"] == 0:
        return LayerResult("cost", "cost/quote-arithmetic", 0, 0,
                           detail="no seeded cases — run seed_cases first", skipped=True)
    return LayerResult("cost", "cost/quote-arithmetic", res["total"], res["passed"],
                       detail=f"{len(res['failures'])} incoherent")
