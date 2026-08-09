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

# Standard Handbook PHP rates per hour (Handbook section 1.1)
HANDBOOK_PHP_RATES: dict[str, Decimal] = {
    "Tech Lead / Solutions Architect": Decimal("3600.00"),
    "Senior Fullstack Engineer": Decimal("2700.00"),
    "QA Automation Manager": Decimal("1800.00"),
    "UI/UX Designer": Decimal("2100.00"),
}

# Standard Base USD rates per hour
BASE_USD_RATES: dict[str, Decimal] = {
    "Tech Lead / Solutions Architect": Decimal("100.00"),
    "Senior Fullstack Engineer": Decimal("75.00"),
    "QA Automation Manager": Decimal("50.00"),
    "UI/UX Designer": Decimal("60.00"),
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


def calculate_role_rate(
    role_name: str,
    target_currency: str = "USD",
    rate: float | Decimal = EXCHANGE_RATE_PESOS_PER_DOLLAR,
) -> tuple[Decimal, str]:
    """Calculate hourly rate for a role in target currency (PHP or USD).

    If target_currency is 'PHP', uses Handbook PHP rates (or converts USD base rates).
    If target_currency is 'USD', converts Handbook PHP rates using 60 Pesos = 1 Dollar.

    Returns:
        tuple[Decimal, str]: (hourly_rate, target_currency_code)
    """
    target_c = normalize_currency_code(target_currency)
    base_usd = BASE_USD_RATES.get(role_name, Decimal("75.00"))
    handbook_php = HANDBOOK_PHP_RATES.get(role_name, base_usd * Decimal("60.0"))

    if target_c == "PHP":
        return (handbook_php, "PHP")
    else:
        usd_rate = convert_currency(handbook_php, "PHP", "USD", rate=rate)
        return (usd_rate, "USD")
