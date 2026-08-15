"""Unit tests for currency_calculator module and conversion logic."""

from decimal import Decimal
import pytest

from stratpoint_rag.currency_calculator import (
    EXCHANGE_RATE_PESOS_PER_DOLLAR,
    HANDBOOK_LICENSE_PHP_ANNUAL,
    HANDBOOK_PHP_RATES,
    calculate_role_rate,
    convert_currency,
    get_category_costings,
    lookup_handbook_rate,
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
    assert rate_php == Decimal("2496.90")

    rate_usd, code_usd = calculate_role_rate("Tech Lead / Solutions Architect", "USD")
    assert code_usd == "USD"
    assert rate_usd == Decimal("41.62")  # 2496.90 / 60 = 41.615 -> 41.62


def test_handbook_role_rates():
    """Verify Section 1.1 role rates in PHP and USD."""
    roles = {
        "Tech Lead / Solutions Architect": Decimal("2496.90"),
        "Solution Architect": Decimal("2740.50"),
        "Senior Fullstack Engineer": Decimal("2090.90"),
        "Senior Frontend Developer": Decimal("1867.60"),
        "Senior Backend Developer": Decimal("1969.10"),
        "QA Automation Manager": Decimal("1299.20"),
        "UI/UX Designer": Decimal("1470.00"),
    }
    for role, expected_php in roles.items():
        assert lookup_handbook_rate(role, target_currency="PHP") == expected_php


def test_handbook_licenses_removed_sections():
    """Verify 5.2 (Gemini), 5.3 (AI Pro), and 1.2b (Frontline/Edu) are not in license dict."""
    assert "google_workspace_starter" in HANDBOOK_LICENSE_PHP_ANNUAL
    assert "google_workspace_standard" in HANDBOOK_LICENSE_PHP_ANNUAL
    assert "google_workspace_plus" in HANDBOOK_LICENSE_PHP_ANNUAL
    assert "gemini_standard" not in HANDBOOK_LICENSE_PHP_ANNUAL
    assert "gemini_plus" not in HANDBOOK_LICENSE_PHP_ANNUAL
    assert "google_ai_pro" not in HANDBOOK_LICENSE_PHP_ANNUAL


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
    """A genuine $600/hr USD rate used to be divided by 60 and printed as $10."""
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


# ── an undeclared estimation currency is inferred, never assumed to be USD ──


_UNDECLARED_PESO_ESTIMATE = {
    "total_cost_usd": 1379994.0,
    "estimated_weeks": 6.6,
    "role_breakdown": [
        {
            "role": "Tech Lead",
            "estimated_hours": 99.0,
            "hourly_rate": 2987.0,
            "total_cost": 295713.0,
        }
    ],
    "phase_timeline": [],
    "summary": "Handbook-Based Estimate: 6.6 weeks for a total investment of PHP 1,379,994.00.",
}


def test_an_undeclared_estimation_currency_is_read_from_the_estimate_itself():
    ctx = build_quote_context(proposal_id="p9", estimation=dict(_UNDECLARED_PESO_ESTIMATE))

    assert ctx.currency_code == "PHP"
    assert ctx.line_items[0].unit_price == Decimal("2987.00")


def test_an_undeclared_currency_survives_the_proposal_input_contract():
    """The real path: `ProposalPDFInput` coerces the dict into an
    `EstimationResult` before `mapping` ever sees it, so a "USD" default on the
    contract is applied silently."""
    from stratpoint_rag.agent.contracts import ProposalPDFInput

    payload = ProposalPDFInput.model_validate(
        {"estimation": dict(_UNDECLARED_PESO_ESTIMATE)}
    )
    ctx = build_quote_context(proposal_id="p10", estimation=payload.estimation)

    assert ctx.currency_code == "PHP"
    assert ctx.line_items[0].unit_price == Decimal("2987.00")
    assert ctx.grand_total_amount < Decimal("1000000")


# ── category costings match whole words, and invent no products ────────────


def test_category_costings_ignore_words_that_merely_contain_a_key():
    """'ai' is inside Email, Plain and Domain — and here it appends *priced*
    line items, not just a label."""
    items = get_category_costings(
        ["Email notifications", "Plain contact form", "Domain registration"],
        ["Web"],
        6.6,
        "PHP",
    )
    assert items == []


def test_category_costings_ignore_data_and_storage_inside_longer_words():
    items = get_category_costings(
        ["Database metadata sync", "Image storagebox"], ["Web"], 6.0, "PHP"
    )
    assert [i["role"] for i in items] == []


def test_genuine_category_keywords_still_bill():
    items = get_category_costings(["LLM chatbot with RAG"], ["Web"], 6.0, "PHP")
    assert any("AI/ML" in i["role"] for i in items)

    cloud = get_category_costings(["Kubernetes deployment"], ["Web"], 6.0, "PHP")
    assert any("DevOps" in i["role"] for i in cloud)


def test_gemini_licence_not_billed_when_removed():
    """Gemini licenses (5.2) are removed from handbook, so category costing only adds AI/ML specialist."""
    named = get_category_costings(["Gemini Enterprise integration"], ["Web"], 6.0, "PHP")
    assert any("AI/ML" in i["role"] for i in named)
    assert not any("Gemini Enterprise AI Software License" in i["role"] for i in named)
