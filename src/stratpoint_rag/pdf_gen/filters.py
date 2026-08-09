"""Jinja filters shared by the quote templates.

Kept out of ``templating.py`` so they are importable and testable on their own —
``slugify`` in particular is used by ``agent/tools.py`` to build the proposal
filename, and a second copy of that rule would drift from this one.

Every filter is total: it returns a printable string for ``None`` rather than
raising. A template that raises mid-render produces no PDF at all, and the
inputs here are partly LLM-derived, so "missing" is a normal value.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

__all__ = ["currency_format", "date_format", "register", "slugify"]

# Human-facing date on the quote header. ISO would be unambiguous but reads as
# machine output on a document a client is meant to sign.
DEFAULT_DATE_FORMAT = "%d %b %Y"

_NON_SLUG = re.compile(r"[^a-z0-9]+")


def currency_format(
    value: Decimal | float | int | str | None,
    symbol: str = "",
    *,
    decimals: int = 2,
) -> str:
    """Thousands-separated fixed-point, optionally prefixed with a symbol.

    ``None`` and unparseable values render as an em dash rather than '0.00':
    a missing price and a free line item are different facts, and printing the
    second when you mean the first understates a quote.
    """
    if value is None or value == "":
        return "—"
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return "—"
    return f"{symbol}{amount:,.{decimals}f}"


def date_format(value: date | datetime | str | None, fmt: str = DEFAULT_DATE_FORMAT) -> str:
    """Format a date, passing strings through untouched.

    A string is assumed to be already formatted by the caller — parsing it here
    would guess at a format and silently transpose day and month.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.strftime(fmt)


def slugify(value: str | None, *, fallback: str = "") -> str:
    """Lowercase, ASCII-ish, underscore-joined — safe as one path component.

    Underscores rather than hyphens to match the existing
    ``stratpoint_proposal_<client>.pdf`` filenames.
    """
    slug = _NON_SLUG.sub("_", (value or "").lower()).strip("_")
    return slug or fallback


def register(env) -> None:
    """Install the filters on a Jinja ``Environment``."""
    env.filters["currency_format"] = currency_format
    env.filters["date_format"] = date_format
    env.filters["slugify"] = slugify
