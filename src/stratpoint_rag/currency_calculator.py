"""Currency conversion calculator for proposal generation.

Handles currency conversions between Philippine Pesos (PHP / ₱) and US Dollars (USD / $)
using the fixed exchange rate: 60 Pesos = 1 Dollar.

Used when there is a currency discrepancy between the source pricing (e.g., Handbook
rates in PHP or base estimates in USD) and the client RFP / brief document currency.
"""

from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

# Default exchange rate: 60 Pesos = 1 Dollar
EXCHANGE_RATE_PESOS_PER_DOLLAR: float = 60.0
DEFAULT_EXCHANGE_RATE: Decimal = Decimal("60.0")

# Standard Handbook PHP rates per hour by role (Handbook Section 1.1)
HANDBOOK_PHP_RATES: dict[str, Decimal] = {
    "Tech Lead / Solutions Architect": Decimal("2496.90"),  # ₱2,233 - ₱2,760.80/hr
    "Solution Architect": Decimal("2740.50"),               # ₱2,436 - ₱3,045/hr
    "Senior Fullstack Engineer": Decimal("2090.90"),        # ₱1,827 - ₱2,354.80/hr
    "Senior Frontend Developer": Decimal("1867.60"),        # ₱1,624 - ₱2,111.20/hr
    "Senior Backend Developer": Decimal("1969.10"),         # ₱1,705.20 - ₱2,233/hr
    "QA Automation Manager": Decimal("1299.20"),            # ₱1,136.80 - ₱1,705.20/hr
    "UI/UX Designer": Decimal("1470.00"),                   # ₱1,218 - ₱1,705.20/hr
}

# Software & License Annual Prices in PHP from Handbook Section 1.2
HANDBOOK_LICENSE_PHP_ANNUAL: dict[str, Decimal] = {
    "google_workspace_starter": Decimal("4527.35"),
    "google_workspace_standard": Decimal("11110.43"),
    "google_workspace_plus": Decimal("14405.75"),
}


def normalize_currency_code(currency: str | None) -> str:
    """Normalize currency string or symbol to 'USD' or 'PHP'."""
    if not currency:
        return "USD"
    s = str(currency).strip().upper()
    if "₱" in str(currency) or s in ("PHP", "PESO", "PESOS", "PH PESO", "PH PESOS"):
        return "PHP"
    if "$" in str(currency) or s in ("USD", "DOLLAR", "DOLLARS", "US DOLLAR", "US DOLLARS"):
        return "USD"
    return "USD"


def convert_currency(
    amount: float | Decimal | int | str,
    from_currency: str,
    to_currency: str,
    rate: float | Decimal = EXCHANGE_RATE_PESOS_PER_DOLLAR,
) -> Decimal:
    """Convert monetary amount between PHP and USD.

    Conversion rule: 60 Pesos = 1 Dollar.

    Args:
        amount: Monetary value to convert.
        from_currency: Source currency ('PHP' / '₱' or 'USD' / '$').
        to_currency: Target currency ('PHP' / '₱' or 'USD' / '$').
        rate: Exchange rate in Pesos per Dollar (default 60.0).

    Returns:
        Converted amount as Decimal rounded to 2 decimal places.
    """
    if amount is None or amount == "":
        return Decimal("0.00")

    try:
        val = Decimal(str(amount))
    except Exception:
        return Decimal("0.00")

    rate_dec = Decimal(str(rate))
    if rate_dec <= 0:
        rate_dec = DEFAULT_EXCHANGE_RATE

    from_c = normalize_currency_code(from_currency)
    to_c = normalize_currency_code(to_currency)

    if from_c == to_c:
        return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if from_c == "PHP" and to_c == "USD":
        converted = val / rate_dec
    elif from_c == "USD" and to_c == "PHP":
        converted = val * rate_dec
    else:
        converted = val

    return converted.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# One whole-word matcher for the whole project. `pdf_gen.mapping` imports it
# rather than compiling its own: the two had drifted apart — one handled
# trailing punctuation and the other did not, and only one of them handled
# plurals — while guarding against the identical defect.
_WORD_CACHE: dict[str, re.Pattern[str]] = {}


def word_in(key: str, text: str) -> bool:
    """True when ``key`` appears in ``text`` as a whole word or phrase.

    Word boundaries on both ends, so "ai" matches "ai" and "ai/ml" but not
    "Email", "Plain" or "Domain". Multi-word keys ("machine learning") match
    across a single run of whitespace, and keys carrying regex metacharacters
    ("e-commerce", "node.js") are escaped.

    An optional plural suffix is allowed, because these keys also sit inside
    *negative* guards: `not has("dashboard", "microservice")` on a brief saying
    "admin dashboards and a microservices backend" matched neither key, so the
    guard let a microservices platform through as a CMS website.
    """
    pattern = _WORD_CACHE.get(key)
    if pattern is None:
        body = r"\s+".join(re.escape(part) for part in key.split())
        pattern = re.compile(rf"(?<!\w){body}(?:e?s)?(?!\w)", re.IGNORECASE)
        _WORD_CACHE[key] = pattern
    return pattern.search(text) is not None


def lookup_handbook_rate(
    role_name: str,
    tech_stack_hints: list[str] | None = None,
    target_currency: str = "USD",
    rate: float | Decimal = EXCHANGE_RATE_PESOS_PER_DOLLAR,
) -> Decimal:
    """Look up exact PHP hourly rate from handbook.md based on role,
    and convert to target_currency (USD or PHP) using 60 Pesos = 1 Dollar rate.
    """
    target_c = normalize_currency_code(target_currency)
    php_rate = HANDBOOK_PHP_RATES.get(role_name, Decimal("2090.90"))

    if target_c == "PHP":
        return php_rate
    else:
        return convert_currency(php_rate, "PHP", "USD", rate=rate)


def calculate_role_rate(
    role_name: str,
    target_currency: str = "USD",
    rate: float | Decimal = EXCHANGE_RATE_PESOS_PER_DOLLAR,
    tech_stack_hints: list[str] | None = None,
) -> tuple[Decimal, str]:
    """Calculate hourly rate for a role in target currency (PHP or USD).

    Refers to handbook.md PHP rates and converts using 60 Pesos = 1 Dollar.

    Returns:
        tuple[Decimal, str]: (hourly_rate, target_currency_code)
    """
    target_c = normalize_currency_code(target_currency)
    converted_rate = lookup_handbook_rate(
        role_name=role_name,
        tech_stack_hints=tech_stack_hints,
        target_currency=target_c,
        rate=rate,
    )
    return (converted_rate, target_c)


def get_category_costings(
    features: list[str],
    target_platform: list[str],
    weeks: float,
    target_currency: str = "USD",
    rate: float | Decimal = EXCHANGE_RATE_PESOS_PER_DOLLAR,
) -> list[dict[str, Any]]:
    """Generate category-specific costing line items referenced from handbook.md
    based on extracted brief features, platforms, and services.
    Returns list of dicts suitable for creating RoleBreakdownItem objects.

    **Keys are matched as whole words**, via the same ``word_in`` the rate
    lookup and ``pdf_gen.mapping`` use.
    """
    target_c = normalize_currency_code(target_currency)
    all_text = " ".join((features or []) + (target_platform or [])).lower()
    additions: list[dict[str, Any]] = []

    def has(*keys: str) -> bool:
        return any(word_in(k, all_text) for k in keys)

    # 1. Cloud & Infrastructure Category (Handbook Section 3)
    has_cloud = has("cloud", "aws", "gcp", "azure", "devops", "infrastructure", "kubernetes", "docker", "sre", "storage")
    if has_cloud:
        devops_php = Decimal("1827.00")  # Senior DevOps / SRE: ₱1,827/hr
        devops_rate = float(convert_currency(devops_php, "PHP", target_c, rate=rate)) if target_c == "USD" else float(devops_php)
        hours = max(15.0, round(weeks * 12.0, 1))
        additions.append({
            "role": "Senior DevOps & Cloud Infrastructure Engineer",
            "estimated_hours": hours,
            "hourly_rate": round(devops_rate, 2),
            "total_cost": round(hours * devops_rate, 2),
        })

        if has("storage", "10tb", "cloud storage", "backup"):
            storage_php = Decimal("93035.63")  # Handbook Section 3: ₱93,035.63/user/yr
            storage_cost = float(convert_currency(storage_php, "PHP", target_c, rate=rate)) if target_c == "USD" else float(storage_php)
            additions.append({
                "role": "Cloud Storage & Backup License Add-on (Annual)",
                "estimated_hours": 1.0,
                "hourly_rate": round(storage_cost, 2),
                "total_cost": round(storage_cost, 2),
            })

    # 2. Artificial Intelligence Category (Handbook Section 5)
    has_ai = has("ai", "ml", "machine learning", "llm", "rag", "model", "gemini", "ai pro", "gpt")
    if has_ai:
        ai_php = Decimal("2537.50")  # Senior AI/ML Engineer: ₱2,537.50/hr (Handbook Section 5.1)
        ai_rate = float(convert_currency(ai_php, "PHP", target_c, rate=rate)) if target_c == "USD" else float(ai_php)
        hours = max(20.0, round(weeks * 15.0, 1))
        additions.append({
            "role": "Senior AI/ML & LLM Integration Specialist",
            "estimated_hours": hours,
            "hourly_rate": round(ai_rate, 2),
            "total_cost": round(hours * ai_rate, 2),
        })

    # 3. Data Services Category (Handbook Section 4)
    has_data = has("data", "etl", "analytics", "pipeline", "data engineering", "data science")
    if has_data and not has_ai:
        data_php = Decimal("1827.00")  # Python Data Developer: ₱1,827/hr
        data_rate = float(convert_currency(data_php, "PHP", target_c, rate=rate)) if target_c == "USD" else float(data_php)
        hours = max(15.0, round(weeks * 10.0, 1))
        additions.append({
            "role": "Senior Data Engineering Specialist",
            "estimated_hours": hours,
            "hourly_rate": round(data_rate, 2),
            "total_cost": round(hours * data_rate, 2),
        })

    # 4. Security & Audit Category (Handbook Section 1.1)
    has_security = has("security", "audit", "compliance", "gdpr", "penetration", "encryption")
    if has_security:
        sec_php = Decimal("2436.00")  # Security Engineer: ₱2,436/hr
        sec_rate = float(convert_currency(sec_php, "PHP", target_c, rate=rate)) if target_c == "USD" else float(sec_php)
        hours = max(10.0, round(weeks * 8.0, 1))
        additions.append({
            "role": "Senior Security & Compliance Engineer",
            "estimated_hours": hours,
            "hourly_rate": round(sec_rate, 2),
            "total_cost": round(hours * sec_rate, 2),
        })

    # 5. Software & Workspace Licenses Category (Handbook Section 1.2)
    has_workspace = has("google workspace", "workspace license", "email license")
    if has_workspace:
        gw_php = Decimal("11110.43")  # Google Workspace Standard: ₱11,110.43/yr
        gw_cost = float(convert_currency(gw_php, "PHP", target_c, rate=rate)) if target_c == "USD" else float(gw_php)
        additions.append({
            "role": "Google Workspace Enterprise License (Annual)",
            "estimated_hours": 1.0,
            "hourly_rate": round(gw_cost, 2),
            "total_cost": round(gw_cost, 2),
        })

    return additions
