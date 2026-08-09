"""Currency conversion calculator for proposal generation.

Handles currency conversions between Philippine Pesos (PHP / ₱) and US Dollars (USD / $)
using the fixed exchange rate: 60 Pesos = 1 Dollar.

Used when there is a currency discrepancy between the source pricing (e.g., Handbook
rates in PHP or base estimates in USD) and the client RFP / brief document currency.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

# Default exchange rate: 60 Pesos = 1 Dollar
EXCHANGE_RATE_PESOS_PER_DOLLAR: float = 60.0
DEFAULT_EXCHANGE_RATE: Decimal = Decimal("60.0")

# Standard Handbook PHP rates per hour by role & tech stack (Handbook Section 1.1)
HANDBOOK_PHP_RATES: dict[str, Decimal] = {
    "Tech Lead / Solutions Architect": Decimal("3567.00"),  # ₱3,190 - ₱3,944/hr
    "Solution Architect": Decimal("3915.00"),               # ₱3,480 - ₱4,350/hr
    "Senior Fullstack Engineer": Decimal("2987.00"),        # ₱2,610 - ₱3,364/hr
    "Senior Frontend Developer": Decimal("2668.00"),        # ₱2,320 - ₱3,016/hr
    "Senior Backend Developer": Decimal("2813.00"),         # ₱2,436 - ₱3,190/hr
    "QA Automation Manager": Decimal("1856.00"),            # ₱1,624 - ₱2,436/hr
    "UI/UX Designer": Decimal("2100.00"),                   # ₱1,740 - ₱2,436/hr
}

# Tech-Stack Specific Senior Rates from Handbook Section 1.1
HANDBOOK_STACK_RATES_PHP: dict[str, Decimal] = {
    "go": Decimal("3567.00"),          # Go: ₱3,190 – ₱3,944/hr
    "golang": Decimal("3567.00"),
    "ai": Decimal("3625.00"),          # Senior AI/ML: ₱3,190 – ₱4,060/hr
    "ml": Decimal("3625.00"),
    "blockchain": Decimal("3857.00"),  # Senior Blockchain: ₱3,364 – ₱4,350/hr
    "security": Decimal("3480.00"),    # Senior Security: ₱3,016 – ₱3,944/hr
    "mobile": Decimal("2987.00"),      # Senior Mobile (iOS/Android): ₱2,610 – ₱3,364/hr
    "ios": Decimal("2987.00"),
    "android": Decimal("2987.00"),
    "next.js": Decimal("3132.00"),     # Next.js/Nuxt: ₱2,784 – ₱3,480/hr
    "nuxt": Decimal("3132.00"),
    "python": Decimal("2987.00"),      # Python: ₱2,610 – ₱3,364/hr
    "angular": Decimal("2987.00"),     # Angular: ₱2,610 – ₱3,364/hr
    "react": Decimal("2813.00"),       # React: ₱2,436 – ₱3,190/hr
    "node.js": Decimal("2813.00"),     # Node.js: ₱2,436 – ₱3,190/hr
    "vue.js": Decimal("2668.00"),      # Vue.js: ₱2,320 – ₱3,016/hr
    "php": Decimal("2494.00"),         # PHP/Laravel: ₱2,204 – ₱2,784/hr
    "laravel": Decimal("2494.00"),
}

# Software & License Annual Prices in PHP from Handbook Section 1.2
HANDBOOK_LICENSE_PHP_ANNUAL: dict[str, Decimal] = {
    "google_workspace_starter": Decimal("6467.64"),
    "google_workspace_standard": Decimal("15872.04"),
    "google_workspace_plus": Decimal("20579.64"),
    "gemini_standard": Decimal("23132.04"),
    "gemini_plus": Decimal("38364.84"),
    "google_ai_pro": Decimal("12574.81"),
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


def lookup_handbook_rate(
    role_name: str,
    tech_stack_hints: list[str] | None = None,
    target_currency: str = "USD",
    rate: float | Decimal = EXCHANGE_RATE_PESOS_PER_DOLLAR,
) -> Decimal:
    """Look up exact PHP hourly rate from handbook.md based on role and tech stack hints,
    and convert to target_currency (USD or PHP) using 60 Pesos = 1 Dollar rate.
    """
    target_c = normalize_currency_code(target_currency)

    # Check for tech stack specific rate in handbook.md
    php_rate = None
    if tech_stack_hints:
        for hint in tech_stack_hints:
            h_clean = hint.strip().lower()
            for key, stack_rate in HANDBOOK_STACK_RATES_PHP.items():
                if key in h_clean:
                    php_rate = stack_rate
                    break
            if php_rate:
                break

    if not php_rate:
        php_rate = HANDBOOK_PHP_RATES.get(role_name, Decimal("2987.00"))

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
