"""The template's input contract: validated, self-totalling quote data.

``ProposalQuoteContext`` is the **only** thing ``quote-template-c.html`` is ever
rendered against. That is the point of it existing: a Jinja template silently
renders an empty string for a name it has never heard of, so a typo in a key —
or an estimator that stops emitting ``role_breakdown`` — produces a
professional-looking quote with a blank column rather than an error. Pydantic
turns that class of failure into a ``ValidationError`` at the boundary.

Three rules hold this shape together:

- **Money is ``Decimal``, never ``float``.** ``0.1 + 0.2`` is the canonical
  float example and this document is a price a client may sign. Every amount is
  quantised to 2dp with ``ROUND_HALF_UP`` at the moment it is computed, so the
  line totals printed in the table always sum to exactly the subtotal printed
  below them. A float pipeline gets that wrong by a cent often enough to be
  noticed and never often enough to be caught in review.

- **Totals are ``@computed_field``, never inputs.** The caller supplies
  quantities and unit prices; subtotal, tax and grand total are derived here.
  Accepting a grand total as a field means the arithmetic can be supplied by an
  LLM, and "the numbers on the quote do not add up" is the single most damaging
  thing this document can do.

- **A missing client name is not an error.** ``client_name`` defaults to a
  generic label for the same reason ``ExtractedRequirements`` has no name field
  at all (see ``docparse/schema.py``): a required name is an instruction to
  invent one, and declining to give a name is an offered choice in
  ``disambiguation/engagement.py``. The generic label is a placeholder, not a
  company.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, Field, computed_field

__all__ = [
    "GENERIC_CLIENT_LABEL",
    "GENERIC_PROJECT_TITLE",
    "LineItem",
    "MilestoneItem",
    "ProposalQuoteContext",
    "money",
]

# Used when the visitor declined to give a name, or never had one asked of them.
# A placeholder, deliberately not a plausible company: "Acme Innovations" on a
# real quote is exactly the hallucination the schema change removed.
GENERIC_CLIENT_LABEL = "Prospective Client"
GENERIC_PROJECT_TITLE = "Project Proposal"

_CENTS = Decimal("0.01")


def money(value: Decimal | float | int | str) -> Decimal:
    """Quantise to 2dp, half-up — the rounding a human doing this by hand uses.

    Applied at every step rather than only at the end, so the line totals shown
    in the table are the exact addends of the subtotal shown beneath them.
    Banker's rounding (Python's ``Decimal`` default) would round 0.005 to the
    even cent and disagree with the client's own spreadsheet.
    """
    return Decimal(str(value)).quantize(_CENTS, rounding=ROUND_HALF_UP)


class LineItem(BaseModel):
    """One row of the cost & deliverable schedule."""

    item_name: str = Field(..., min_length=1, max_length=160)
    description: str | None = Field(
        None, max_length=400, description="Sub-line under the item name."
    )
    quantity: Decimal = Field(..., ge=0, description="Units billed (e.g. hours).")
    unit: str | None = Field(
        None, max_length=24, description="Unit label shown after the quantity, e.g. 'hrs'."
    )
    unit_price: Decimal = Field(..., ge=0, description="Price per unit, in the quote's currency.")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_amount(self) -> Decimal:
        """quantity x unit_price. The row's authoritative number."""
        return money(self.quantity * self.unit_price)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def formatted_quantity(self) -> str:
        """Trailing zeros dropped: '60' not '60.00', '7.5' not '7.50'.

        The template prints this rather than the raw ``Decimal``, whose repr
        carries whatever scale the caller happened to construct it with — a
        quantity built from ``weeks * 15`` renders as '112.5' and one built from
        an int renders as '112', in the same column.
        """
        # normalize() can hand back exponent form (Decimal('1E+2')); format(_, "f")
        # is what forces it back to '100'.
        text = format(self.quantity.normalize(), "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return f"{text} {self.unit}" if self.unit else text

    @computed_field  # type: ignore[prop-decorator]
    @property
    def formatted_unit_price(self) -> str:
        """Thousands-separated, 2dp — no currency symbol (the template adds it)."""
        return f"{money(self.unit_price):,.2f}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def formatted_total(self) -> str:
        return f"{self.total_amount:,.2f}"


class MilestoneItem(BaseModel):
    """One phase of the delivery roadmap — a chevron plus its detail card."""

    phase_number: int = Field(..., ge=1)
    phase_name: str = Field(
        ...,
        min_length=1,
        max_length=40,
        description="Short label for the chevron ribbon. Capped: the chevrons "
        "share one row, so a long label overflows its clip-path.",
    )
    title: str = Field(..., min_length=1, max_length=120)
    duration: str = Field("", max_length=40, description="e.g. '2.4 wks'.")
    description: str = Field("", max_length=600)
    deliverable: str = Field("", max_length=300)
    date_range: str | None = Field(None, max_length=80)


class ProposalQuoteContext(BaseModel):
    """Everything ``quote-template-c.html`` reads. Nothing else is in scope."""

    # ── identity & dates ────────────────────────────────────────────────
    quote_number: str = Field(..., min_length=1, max_length=40)
    quote_date: date
    valid_until: date

    currency_symbol: str = Field("$", min_length=1, max_length=4)
    currency_code: str = Field("USD", min_length=3, max_length=3)

    # ── provider ────────────────────────────────────────────────────────
    company_name: str = Field("Stratpoint Technologies", min_length=1, max_length=120)
    company_subtitle: str | None = Field(None, max_length=160)
    company_address: str | None = Field(None, max_length=200)
    company_email: str | None = Field(None, max_length=120)
    company_phone: str | None = Field(None, max_length=60)
    company_website: str | None = Field(None, max_length=120)
    # A file path is never accepted here — only a data: URI or an already-inlined
    # asset. The renderer blocks the network, so an http(s) logo silently
    # disappears from the PDF rather than failing loudly. See pdf_gen/assets.py.
    logo_url: str | None = None

    # ── client ──────────────────────────────────────────────────────────
    client_name: str = Field(GENERIC_CLIENT_LABEL, min_length=1, max_length=160)
    client_company: str | None = Field(None, max_length=160)
    client_address: str | None = Field(None, max_length=200)
    client_email: str | None = Field(None, max_length=120)
    client_phone: str | None = Field(None, max_length=60)

    # ── scope ───────────────────────────────────────────────────────────
    project_title: str | None = Field(None, max_length=160)
    project_description: str | None = Field(None, max_length=1200)

    # min_length=1: a quote with no priced rows is not a quote. The caller must
    # decide what to do about an empty estimate; rendering a blank table and
    # a $0.00 grand total is not one of the acceptable options.
    line_items: list[LineItem] = Field(..., min_length=1)
    tax_rate_percent: Decimal = Field(
        Decimal("0"), ge=0, le=100, description="0 hides the tax row entirely."
    )

    milestones: list[MilestoneItem] = Field(default_factory=list)

    notes: str | None = Field(None, max_length=1200)
    payment_terms: str | None = Field(None, max_length=1200)

    # ── derived money ───────────────────────────────────────────────────

    @computed_field  # type: ignore[prop-decorator]
    @property
    def subtotal_amount(self) -> Decimal:
        return money(sum((i.total_amount for i in self.line_items), Decimal("0")))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tax_amount(self) -> Decimal:
        return money(self.subtotal_amount * self.tax_rate_percent / Decimal("100"))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def grand_total_amount(self) -> Decimal:
        return money(self.subtotal_amount + self.tax_amount)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tax_rate_label(self) -> str | None:
        """'12%' — or None at a zero rate, which is what hides the whole row.

        The template gates on this being falsy. A formatted *string* would be
        truthy at "0.00" and print a pointless zero-tax line on every quote.
        """
        if self.tax_rate_percent == 0:
            return None
        rate = self.tax_rate_percent.normalize()
        text = format(rate, "f")
        return f"{text.rstrip('0').rstrip('.') if '.' in text else text}%"
