"""Unit tests for currency_calculator module and conversion logic."""

from decimal import Decimal
import pytest

from stratpoint_rag.currency_calculator import (
    EXCHANGE_RATE_PESOS_PER_DOLLAR,
    calculate_role_rate,
    convert_currency,
    get_category_costings,
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


def test_tech_stack_handbook_rate():
    # Go Senior rate from handbook.md (₱2,496.90/hr PHP -> $41.62/hr USD)
    rate_go, _ = calculate_role_rate("Senior Fullstack Engineer", "USD", tech_stack_hints=["Go Backend"])
    assert rate_go == Decimal("41.62")

    # React Senior rate from handbook.md (₱1,969.10/hr PHP -> $32.82/hr USD)
    rate_react, _ = calculate_role_rate("Senior Fullstack Engineer", "USD", tech_stack_hints=["React SPA"])
    assert rate_react == Decimal("32.82")

    # AI/ML Senior rate from handbook.md (₱2,537.50/hr PHP -> $42.29/hr USD)
    rate_ai, _ = calculate_role_rate("Senior Fullstack Engineer", "USD", tech_stack_hints=["AI Model Tuning"])
    assert rate_ai == Decimal("42.29")


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
    """A plain CRUD website must not bill at the senior AI/ML rate.

    Asked of a role the stack table *is* about, so the assertion is about
    tokenisation and not about role eligibility — see
    ``test_a_shared_stack_hint_does_not_override_a_non_engineering_role``.
    """
    rate, _ = calculate_role_rate("Senior Frontend Developer", "PHP", tech_stack_hints=[hint])
    assert rate == Decimal("1867.60")  # the role's own handbook rate


@pytest.mark.parametrize(
    "hint,expected",
    [
        ("Go Backend", "2496.90"),
        ("React SPA", "1969.10"),
        ("AI Model Tuning", "2537.50"),
        ("Next.js frontend", "2192.40"),
        ("Python/Django API", "2090.90"),
        ("Vue.js dashboard", "1867.60"),
    ],
)
def test_real_stack_hints_still_win(hint, expected):
    """The override itself must keep working — including dotted keys."""
    rate, _ = calculate_role_rate("Senior Frontend Developer", "PHP", tech_stack_hints=[hint])
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


# ── an undeclared estimation currency is inferred, never assumed to be USD ──
#
# The model re-supplies an estimation as a *dict* copied out of a prior
# Observation, and that dict predates `currency_code`. With the field defaulting
# to "USD" the peso amounts inside it were relabelled dollars and multiplied by
# 60 on the way to a peso quote: PHP 2,987.00/hr printed as PHP 179,220.00/hr.


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


# ── the stack table is consulted for the roles it is about ─────────────────
#
# Handbook 1.1's stack rates are labelled "Senior <stack> Engineer". One shared
# hint list is passed for every role, so testing the table before the role's own
# rate billed a QA manager (+61%) and a designer (+42%) at senior mobile rates.


def test_a_shared_stack_hint_does_not_override_a_non_engineering_role():
    hints = ["User login", "Product catalog", "Mobile"]

    assert calculate_role_rate("QA Automation Manager", "PHP", tech_stack_hints=hints)[0] == Decimal("1299.20")
    assert calculate_role_rate("UI/UX Designer", "PHP", tech_stack_hints=hints)[0] == Decimal("1470.00")
    assert calculate_role_rate("Tech Lead / Solutions Architect", "PHP", tech_stack_hints=hints)[0] == Decimal("2496.90")
    # The engineer the "Mobile" hint is actually about still moves.
    assert calculate_role_rate("Senior Frontend Developer", "PHP", tech_stack_hints=hints)[0] == Decimal("2090.90")


@pytest.mark.parametrize(
    "hint,expected",
    [
        ("Backend in Python.", "2090.90"),
        ("Built on blockchain.", "2699.90"),
        ("Frontend in React.", "1969.10"),
    ],
)
def test_a_trailing_full_stop_does_not_hide_a_stack_token(hint, expected):
    """`tech_hints` is LLM-written prose that ends sentences; the token regex
    admitted `.` so `next.js` would survive and swallowed the full stop too."""
    rate, _ = calculate_role_rate("Senior Frontend Developer", "PHP", tech_stack_hints=[hint])
    assert rate == Decimal(expected)


def test_the_first_matching_hint_wins_not_the_first_table_key():
    """Flattening every hint into one set discarded hint order, so the winner
    became whichever key is declared first in `HANDBOOK_STACK_RATES_PHP`."""
    rate, _ = calculate_role_rate(
        "Senior Frontend Developer",
        "PHP",
        tech_stack_hints=["React SPA dashboard", "Python reporting API"],
    )
    assert rate == Decimal("1969.10")  # React — the first hint that matched


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


def test_a_gemini_licence_is_only_billed_when_the_brief_names_it():
    """Nothing on the page is invented — a line item for a named third-party
    product is a feature the brief never asked for."""
    generic = get_category_costings(["LLM chatbot with RAG"], ["Web"], 6.0, "PHP")
    assert not any("Gemini" in i["role"] for i in generic)

    named = get_category_costings(["Gemini Enterprise integration"], ["Web"], 6.0, "PHP")
    assert any("Gemini" in i["role"] for i in named)
