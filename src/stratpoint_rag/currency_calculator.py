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


# Stack hints arrive as free-text feature descriptions from an LLM, so they are
# tokenised before they are matched. Substring matching over keys this short is
# indefensible: "ai" is inside "Email", "Plain" and "Domain", "ml" is inside
# "HTML", and "go" is inside "Google" and "Logo". Measured before the fix, a
# UI/UX Designer on a brief whose features said "Plain CRUD forms" billed at the
# senior AI/ML rate — PHP 3,625.00/hr against their own PHP 2,100.00/hr, a 73%
# overcharge on a plain CRUD website.
#
# Dots, plus and hash are kept *inside* a token so "next.js", "node.js",
# "vue.js" and languages like "c#"/"c++" survive tokenisation as single terms —
# but a token may not *end* on one, or a hint ending a sentence loses its stack:
# "Backend in Python." tokenised to "python." and fell through to the base role
# rate, ~16% under. `tech_stack_hints` is LLM-written prose, so trailing
# punctuation is the common case, not the edge case.
_STACK_TOKEN_RE = re.compile(r"[a-z0-9](?:[a-z0-9.+#]*[a-z0-9+#])?")


def _stack_tokens(hints: list[str] | None) -> set[str]:
    """Every whole word across the hints, lowercased."""
    tokens: set[str] = set()
    for hint in hints or []:
        tokens.update(_STACK_TOKEN_RE.findall(str(hint).lower()))
    return tokens


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


# Handbook Section 1.1's stack rates are labelled "Senior <stack> Engineer" —
# they are specialisations of a *senior engineer's* rate, not a multiplier for
# everyone on the team. `agent/tools.py` passes one shared hint list
# (`features + target_platform`) for every role, so testing the table ahead of
# the role's own rate billed a whole team at one stack rate: on a brief whose
# hints were ["User login", "Product catalog", "Mobile"], the QA Automation
# Manager billed PHP 2,987.00 against their own PHP 1,856.00 (+61%) and the
# UI/UX Designer PHP 2,987.00 against PHP 2,100.00 (+42%).
_STACK_RATE_ROLES = frozenset(
    {
        "senior fullstack engineer",
        "senior frontend developer",
        "senior backend developer",
    }
)


def _stack_rate_for(tech_stack_hints: list[str] | None) -> Decimal | None:
    """The first *hint* that names a stack, not the first key in the table.

    Hint order is the caller's statement of priority; table order is an
    implementation detail. Flattening every hint into one set discarded the
    former and silently substituted the latter — ``["React SPA dashboard",
    "Python reporting API"]`` returned Python's rate because ``python`` happens
    to be declared above ``react`` here, moving every role on the quote ~6%.
    """
    for hint in tech_stack_hints or []:
        tokens = _stack_tokens([hint])
        if not tokens:
            continue
        for key, stack_rate in HANDBOOK_STACK_RATES_PHP.items():
            if key in tokens:
                return stack_rate
    return None


def lookup_handbook_rate(
    role_name: str,
    tech_stack_hints: list[str] | None = None,
    target_currency: str = "USD",
    rate: float | Decimal = EXCHANGE_RATE_PESOS_PER_DOLLAR,
) -> Decimal:
    """Look up exact PHP hourly rate from handbook.md based on role and tech stack hints,
    and convert to target_currency (USD or PHP) using 60 Pesos = 1 Dollar rate.

    **The role's own handbook rate wins.** A stack hint overrides it only for
    the senior engineering roles the stack table is about (``_STACK_RATE_ROLES``)
    and only on a **whole-token** match — see ``_STACK_TOKEN_RE`` for what
    substring matching cost here.
    """
    target_c = normalize_currency_code(target_currency)

    php_rate = None
    if role_name.strip().casefold() in _STACK_RATE_ROLES:
        # Iterating the hint tokens against the rate table (rather than the
        # table against the text) means a hint matches a key only when it *is*
        # that key.
        php_rate = _stack_rate_for(tech_stack_hints)

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
    lookup and ``pdf_gen.mapping`` use. Substring matching here is worse than it
    was eighty lines above: this function appends *priced line items*, so
    ["Email notifications", "Plain contact form", "Domain registration"] — "ai"
    inside each — added a Senior AI/ML Specialist and a software licence,
    ~PHP 382,000 of work nobody asked for, straight into ``role_breakdown`` and
    onto the client's PDF.
    """
    target_c = normalize_currency_code(target_currency)
    all_text = " ".join((features or []) + (target_platform or [])).lower()
    additions: list[dict[str, Any]] = []

    def has(*keys: str) -> bool:
        return any(word_in(k, all_text) for k in keys)

    # 1. Cloud & Infrastructure Category (Handbook Section 3)
    has_cloud = has("cloud", "aws", "gcp", "azure", "devops", "infrastructure", "kubernetes", "docker", "sre", "storage")
    if has_cloud:
        devops_php = Decimal("2610.00")  # Senior DevOps / SRE: ₱2,610/hr
        devops_rate = float(convert_currency(devops_php, "PHP", target_c, rate=rate)) if target_c == "USD" else float(devops_php)
        hours = max(15.0, round(weeks * 12.0, 1))
        additions.append({
            "role": "Senior DevOps & Cloud Infrastructure Engineer",
            "estimated_hours": hours,
            "hourly_rate": round(devops_rate, 2),
            "total_cost": round(hours * devops_rate, 2),
        })

        if has("storage", "10tb", "cloud storage", "backup"):
            storage_php = Decimal("132908.04")  # Handbook Section 3: ₱132,908.04/user/yr
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
        ai_php = Decimal("3625.00")  # Senior AI/ML Engineer: ₱3,625/hr
        ai_rate = float(convert_currency(ai_php, "PHP", target_c, rate=rate)) if target_c == "USD" else float(ai_php)
        hours = max(20.0, round(weeks * 15.0, 1))
        additions.append({
            "role": "Senior AI/ML & LLM Integration Specialist",
            "estimated_hours": hours,
            "hourly_rate": round(ai_rate, 2),
            "total_cost": round(hours * ai_rate, 2),
        })

        # Only when the brief names the product. A line item for a named
        # third-party licence is a *feature*, and pdf_gen's rule is that nothing
        # on the page is invented: everything is supplied by the caller, read
        # out of the two contracts, or a documented constant. Billing every
        # AI-flagged brief for Gemini invented one at PHP 23,132.04/yr.
        if has("gemini", "google ai", "ai pro"):
            gemini_php = Decimal("23132.04")  # Gemini Standard License: ₱23,132.04/yr
            gemini_cost = float(convert_currency(gemini_php, "PHP", target_c, rate=rate)) if target_c == "USD" else float(gemini_php)
            additions.append({
                "role": "Gemini Enterprise AI Software License (Annual)",
                "estimated_hours": 1.0,
                "hourly_rate": round(gemini_cost, 2),
                "total_cost": round(gemini_cost, 2),
            })

    # 3. Data Services Category (Handbook Section 4)
    has_data = has("data", "etl", "analytics", "pipeline", "data engineering", "data science")
    if has_data and not has_ai:
        data_php = Decimal("2610.00")  # Python Data Developer: ₱2,610/hr
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
        sec_php = Decimal("3480.00")  # Security Engineer: ₱3,480/hr
        sec_rate = float(convert_currency(sec_php, "PHP", target_c, rate=rate)) if target_c == "USD" else float(sec_php)
        hours = max(10.0, round(weeks * 8.0, 1))
        additions.append({
            "role": "Senior Security & Compliance Engineer",
            "estimated_hours": hours,
            "hourly_rate": round(sec_rate, 2),
            "total_cost": round(hours * sec_rate, 2),
        })

    # 5. Software & Workspace Licenses Category (Handbook Section 1.2)
    has_workspace = has("google workspace", "workspace license", "email license", "google frontline")
    if has_workspace:
        gw_php = Decimal("15872.04")  # Google Workspace Standard: ₱15,872.04/yr
        gw_cost = float(convert_currency(gw_php, "PHP", target_c, rate=rate)) if target_c == "USD" else float(gw_php)
        additions.append({
            "role": "Google Workspace Enterprise License (Annual)",
            "estimated_hours": 1.0,
            "hourly_rate": round(gw_cost, 2),
            "total_cost": round(gw_cost, 2),
        })

    return additions
