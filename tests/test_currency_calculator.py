"""Unit tests for currency_calculator module and conversion logic."""

from decimal import Decimal
import pytest

from stratpoint_rag.currency_calculator import (
    EXCHANGE_RATE_PESOS_PER_DOLLAR,
    calculate_role_rate,
    convert_currency,
    normalize_currency_code,
)
from stratpoint_rag.agent.contracts import EstimationResult, RoleBreakdownItem
from stratpoint_rag.pdf_gen.mapping import build_quote_context


def test_normalize_currency_code():
    assert normalize_currency_code("USD") == "USD"
    assert normalize_currency_code("$") == "USD"
    assert normalize_currency_code("PHP") == "PHP"
    assert normalize_currency_code("₱") == "PHP"
    assert normalize_currency_code("pesos") == "PHP"
    assert normalize_currency_code("PhP") == "PHP"
    assert normalize_currency_code(None) == "USD"


def test_convert_currency_php_to_usd():
    # 60 Pesos = 1 Dollar
    assert convert_currency(60, "PHP", "USD") == Decimal("1.00")
    assert convert_currency(6000, "PHP", "USD") == Decimal("100.00")
    assert convert_currency(1500, "₱", "$") == Decimal("25.00")


def test_convert_currency_usd_to_php():
    # 1 Dollar = 60 Pesos
    assert convert_currency(1, "USD", "PHP") == Decimal("60.00")
    assert convert_currency(100, "USD", "PHP") == Decimal("6000.00")
    assert convert_currency(25, "$", "₱") == Decimal("1500.00")


def test_convert_currency_same_currency():
    assert convert_currency(100, "USD", "USD") == Decimal("100.00")
    assert convert_currency(5000, "PHP", "PHP") == Decimal("5000.00")


def test_calculate_role_rate():
    rate_php, code_php = calculate_role_rate("Tech Lead / Solutions Architect", "PHP")
    assert code_php == "PHP"
    assert rate_php == Decimal("3567.00")

    rate_usd, code_usd = calculate_role_rate("Tech Lead / Solutions Architect", "USD")
    assert code_usd == "USD"
    assert rate_usd == Decimal("59.45")  # 3567 / 60 = 59.45


def test_tech_stack_handbook_rate():
    # Go Senior rate from handbook.md (₱3,567/hr PHP -> $59.45/hr USD)
    rate_go, _ = calculate_role_rate("Senior Fullstack Engineer", "USD", tech_stack_hints=["Go Backend"])
    assert rate_go == Decimal("59.45")

    # React Senior rate from handbook.md (₱2,813/hr PHP -> $46.88/hr USD)
    rate_react, _ = calculate_role_rate("Senior Fullstack Engineer", "USD", tech_stack_hints=["React SPA"])
    assert rate_react == Decimal("46.88")

    # AI/ML Senior rate from handbook.md (₱3,625/hr PHP -> $60.42/hr USD)
    rate_ai, _ = calculate_role_rate("Senior Fullstack Engineer", "USD", tech_stack_hints=["AI Model Tuning"])
    assert rate_ai == Decimal("60.42")


def test_discrepancy_conversion_in_mapping():
    # Estimator returns rates in USD (e.g. $100/hr)
    estimation = EstimationResult(
        total_cost_usd=10000.0,
        estimated_weeks=5.0,
        role_breakdown=[
            RoleBreakdownItem(
                role="Tech Lead",
                estimated_hours=100.0,
                hourly_rate=100.0,  # $100/hr USD
                total_cost=10000.0,
            )
        ],
        phase_timeline=[],
        summary="Estimated cost $10,000 USD",
    )

    # Document / brief is in PHP
    ctx_php = build_quote_context(
        proposal_id="proposal123",
        estimation=estimation,
        requirements={"constraints": ["Budget is 600,000 PHP"]},
    )

    assert ctx_php.currency_symbol == "₱"
    assert ctx_php.currency_code == "PHP"
    # Unit price converted from $100 USD to ₱6,000 PHP (100 * 60)
    assert ctx_php.line_items[0].unit_price == Decimal("6000.00")
    assert ctx_php.grand_total_amount == Decimal("600000.00")


# ── stack hints are matched as whole tokens ────────────────────────────────
#
# These keys are two and three characters long and are matched against free-text
# feature descriptions written by an LLM. Substring matching put "ai" inside
# "Email"/"Plain"/"Domain", "ml" inside "HTML" and "go" inside "Google".


@pytest.mark.parametrize(
    "hint",
    [
        "Email notifications",   # 'ai' inside "Email"
        "Domain registration",   # 'ai' inside "Domain"
        "Plain CRUD forms",      # 'ai' inside "Plain"
        "HTML export",           # 'ml' inside "HTML"
        "Google Maps integration",  # 'go' inside "Google"
        "Company logo upload",   # 'go' inside "logo"
    ],
)
def test_ordinary_words_do_not_trigger_a_stack_rate(hint):
    """A plain CRUD website must not bill at the senior AI/ML rate."""
    rate, _ = calculate_role_rate("UI/UX Designer", "PHP", tech_stack_hints=[hint])
    assert rate == Decimal("2100.00")  # the designer's own handbook rate


@pytest.mark.parametrize(
    "hint,expected",
    [
        ("Go Backend", "3567.00"),
        ("React SPA", "2813.00"),
        ("AI Model Tuning", "3625.00"),
        ("Next.js frontend", "3132.00"),
        ("Python/Django API", "2987.00"),
        ("Vue.js dashboard", "2668.00"),
    ],
)
def test_real_stack_hints_still_win(hint, expected):
    """The override itself must keep working — including dotted keys."""
    rate, _ = calculate_role_rate("UI/UX Designer", "PHP", tech_stack_hints=[hint])
    assert rate == Decimal(expected)


# ── line items convert on the declared code, not the magnitude ─────────────


def _estimate(hourly_rate: float, currency_code: str) -> EstimationResult:
    return EstimationResult(
        total_cost_usd=hourly_rate * 100,
        currency_code=currency_code,
        estimated_weeks=5.0,
        role_breakdown=[
            RoleBreakdownItem(
                role="Tech Lead", estimated_hours=100.0,
                hourly_rate=hourly_rate, total_cost=hourly_rate * 100,
            )
        ],
        phase_timeline=[],
        summary="Estimate",
    )


def test_high_usd_rate_is_not_mistaken_for_pesos():
    """A genuine $600/hr USD rate used to be divided by 60 and printed as $10.

    The old rule inferred the source currency from the number's size: under 500
    meant dollars, 500 or more meant pesos. That holds for the current handbook
    rates and for nothing else.
    """
    ctx = build_quote_context(
        proposal_id="p1",
        estimation=_estimate(600.0, "USD"),
        requirements={"constraints": ["Budget in USD"]},
    )
    assert ctx.currency_code == "USD"
    assert ctx.line_items[0].unit_price == Decimal("600.00")


def test_low_php_rate_is_not_mistaken_for_dollars():
    """The mirror case: a PHP rate under 500/hr must not be multiplied by 60."""
    ctx = build_quote_context(
        proposal_id="p2",
        estimation=_estimate(400.0, "PHP"),
        requirements={"constraints": ["Budget is 600,000 pesos"]},
    )
    assert ctx.currency_code == "PHP"
    assert ctx.line_items[0].unit_price == Decimal("400.00")


def test_php_estimate_converts_to_a_usd_quote():
    ctx = build_quote_context(
        proposal_id="p3",
        estimation=_estimate(3000.0, "PHP"),
        requirements={"constraints": ["Budget in USD"]},
    )
    assert ctx.currency_code == "USD"
    assert ctx.line_items[0].unit_price == Decimal("50.00")  # 3000 / 60
