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
