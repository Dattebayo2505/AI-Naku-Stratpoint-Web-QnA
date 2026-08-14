"""Agent output -> ``ProposalQuoteContext``.

The one place that knows how an ``EstimationResult`` and an
``ExtractedRequirements`` become a quote. Kept separate from ``templating`` so
the template contract can be tested without the agent contracts, and from
``agent/tools.py`` so the mapping can be exercised without a browser.

Three rules:

- **Nothing is invented.** No client name, no company, no address, no feature.
  Everything on the page is either supplied by the caller, read out of the two
  contracts, or a documented constant from ``config.py``. This is the same rule
  ``ExtractedRequirements`` enforces by having no name field at all.

- **An empty estimate is an error, not a $0 quote.** ``ProposalQuoteContext``
  requires at least one line item, and this module refuses to synthesise one
  from nothing. A quote whose grand total is $0.00 because the estimator
  returned no roles is worse than a failed tool call: it is confidently wrong
  and looks finished.

- **Lost pages travel with the price.** If hop 1 could not read pages of the
  brief, that lands in the quote's notes. A proposal built on a brief where
  vision choked on 6 of 20 pages must not read like one built on a clean brief —
  the same reason ``_format_requirements`` puts page accounting in the loop's
  Observation.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from stratpoint_rag.agent.contracts import EstimationResult, ExtractedRequirements
from stratpoint_rag.currency_calculator import (
    convert_currency,
    normalize_currency_code,
    word_in as _word_in,
)
from stratpoint_rag.docparse.extract import declared_currency, detect_currency
from stratpoint_rag.pdf_gen import config
from stratpoint_rag.pdf_gen.assets import data_uri
from stratpoint_rag.pdf_gen.schema import (
    GENERIC_CLIENT_LABEL,
    GENERIC_PROJECT_TITLE,
    LineItem,
    MilestoneItem,
    ProposalQuoteContext,
    money,
)

__all__ = ["EmptyEstimate", "build_quote_context", "quote_number_for"]

# Chevron labels share one row inside a fixed 52px bar. At six phases — the most
# the template's colour rules cover — each bar is ~125px wide, which is ~12
# characters per line at 12px bold, and two lines is all the bar height allows.
_CHEVRON_LABEL_CHARS = 40

_PHASE_PREFIX = re.compile(r"^\s*(phase\s*\d+\s*[:.\-–]\s*)", re.IGNORECASE)

# Category keys are matched as whole words, by the one matcher the rate lookup
# uses too (`currency_calculator.word_in`, imported above). Two copies of this
# rule had already drifted: only one of them tolerated a trailing full stop, and
# neither matched plurals — which mattered most inside the negative guard below.


class EmptyEstimate(ValueError):
    """The estimation carried no priced work, so there is nothing to quote."""


def quote_number_for(proposal_id: str, today: date) -> str:
    """``SP-20260809-A1B2C3`` — sortable by date, unique by proposal id.

    Derived, not counted: a running sequence number needs shared state the
    container does not have, and two uvicorn workers would hand out the same
    one.
    """
    return f"SP-{today:%Y%m%d}-{proposal_id[:6].upper()}"


def _as_estimation(value: EstimationResult | dict[str, Any] | None) -> EstimationResult | None:
    if value is None:
        return None
    if isinstance(value, EstimationResult):
        return value
    if isinstance(value, dict) and value:
        return EstimationResult.model_validate(value)
    return None


def _as_requirements(
    value: ExtractedRequirements | dict[str, Any] | None,
) -> ExtractedRequirements | None:
    if value is None:
        return None
    if isinstance(value, ExtractedRequirements):
        return value
    if isinstance(value, dict) and value:
        # Requirements arrive from an LLM path and may carry keys this contract
        # dropped (client_name, project_name). Ignore them rather than 500 the
        # proposal: the schema is the filter, not the caller.
        known = {k: v for k, v in value.items() if k in ExtractedRequirements.model_fields}
        return ExtractedRequirements.model_validate(known)
    return None


def _source_currency(estimation: EstimationResult, target_currency: str) -> str:
    """Which currency ``estimation``'s amounts are in.

    Read off ``currency_code`` when the payload declares one. It often does not:
    the model re-supplies an estimation as a dict copied out of an Observation
    written before that field existed, and every such dict used to take the
    contract's "USD" default. On a peso engagement that default is a **60x**
    error in the client's favour-of-nobody — PHP 2,987.00/hr printed as
    PHP 179,220.00/hr — and it is silent, because a relabelled number is still a
    well-formed number.

    Undeclared, the payload is asked rather than assumed: its own summary line
    ("a total investment of PHP 1,379,994.00") is written by the estimator and
    names the currency it priced in. Failing even that, the amounts are taken to
    be in the quote's own currency: relabelling costs nothing, while dividing or
    multiplying by sixty on no evidence is the error this whole function exists
    to prevent.
    """
    declared = getattr(estimation, "currency_code", None)
    if declared:
        return normalize_currency_code(declared)

    inferred = declared_currency(estimation.summary)
    return inferred or normalize_currency_code(target_currency)


def _line_items(
    estimation: EstimationResult | None,
    target_currency: str = "USD",
) -> list[LineItem]:
    """One row per role. Hours x rate, so the table's arithmetic is visible.

    ``RoleBreakdownItem`` also carries ``total_cost``; it is deliberately not
    used as the row total. A quote whose Qty x Unit Price does not equal its
    own Line Total is the fastest way to lose a client's trust in every other
    number on the page, and when the two disagree the printed factors are the
    ones the reader can check.

    **The source currency is read, never guessed.** This used to infer it from
    the size of the number — under 500 meant dollars, 500 or more meant pesos —
    which happens to hold for the current handbook rates and for nothing else. A
    genuine USD rate of 600/hr was divided by 60 and printed as $10.00/hr. The
    estimator has always known which currency it priced in; it now says so on
    ``EstimationResult.currency_code`` and this reads it.
    """
    if estimation is None:
        return []

    source_currency = _source_currency(estimation, target_currency)

    items: list[LineItem] = []
    for role in estimation.role_breakdown:
        if role.estimated_hours <= 0 or role.hourly_rate <= 0:
            continue

        rate = Decimal(str(role.hourly_rate))
        if source_currency != target_currency:
            rate = convert_currency(role.hourly_rate, source_currency, target_currency)

        unit_str = "license" if any(k in role.role.lower() for k in ("license", "add-on")) else "hrs"
        items.append(
            LineItem(
                item_name=role.role,
                quantity=money(role.estimated_hours),
                unit=unit_str,
                unit_price=money(rate),
            )
        )
    if items:
        return items

    # No role breakdown, but a real total: quote it as one fixed-scope line
    # rather than dropping the number the estimator did produce. Same rule as
    # above — convert on the declared code. The old branch here keyed on the
    # substring "PHP" appearing in the estimator's free-text summary prose.
    if estimation.total_cost_usd > 0:
        total_val = Decimal(str(estimation.total_cost_usd))
        if source_currency != target_currency:
            total_val = convert_currency(
                estimation.total_cost_usd, source_currency, target_currency
            )

        return [
            LineItem(
                item_name="Project delivery — fixed scope",
                description=estimation.summary[:400] or None,
                quantity=Decimal("1"),
                unit_price=money(total_val),
            )
        ]
    return []


def _short_phase(name: str) -> str:
    """Chevron ribbon badge label.

    Extracts a punchy summary for the ribbon badge so text fits cleanly
    without vertical word-breaking or mid-word truncation.
    """
    stripped = _PHASE_PREFIX.sub("", name).strip() or name.strip()
    if "—" in stripped:
        parts = stripped.split("—")
        title = parts[1].strip() if len(parts[1].strip()) >= 3 else parts[0].strip()
        stripped = title
    elif ":" in stripped:
        parts = stripped.split(":")
        title = parts[1].strip() if len(parts[1].strip()) >= 3 else parts[0].strip()
        stripped = title

    max_chars = 35
    if len(stripped) <= max_chars:
        return stripped


    head = stripped[: max_chars - 1]
    cut = head.rsplit(" ", 1)[0] if " " in head else head
    return cut.rstrip(" ,;-&") + "…"



def _milestones(estimation: EstimationResult | None, start: date) -> list[MilestoneItem]:
    """Phases in order, with date ranges accumulated from ``start``.

    Dates are derived from the durations rather than asked of the model: a
    roadmap whose phase dates do not chain from one to the next is the kind of
    error a reader spots immediately and cannot un-see.
    """
    if estimation is None:
        return []

    out: list[MilestoneItem] = []
    cursor = start
    for i, phase in enumerate(estimation.phase_timeline, start=1):
        weeks = max(0.0, float(phase.duration_weeks))
        end = cursor + timedelta(weeks=weeks)
        title = _PHASE_PREFIX.sub("", phase.phase_name).strip() or phase.phase_name.strip()
        out.append(
            MilestoneItem(
                phase_number=i,
                phase_name=_short_phase(phase.phase_name),
                title=title[:120],
                duration=f"{weeks:g} wks",
                description="; ".join(phase.milestones)[:600] or "Scope per statement of work.",
                deliverable=(phase.milestones[0] if phase.milestones else "Phase sign-off")[:300],
                date_range=f"{cursor:%d %b} – {end:%d %b %Y}",
            )
        )
        cursor = end
    return out


def infer_project_title(
    requirements: ExtractedRequirements | None,
    project_name: str | None = None,
) -> str:
    """Infer proposal project title mapped directly to Stratpoint's official Handbook Service Categories:
    1. Software Services (Web, Mobile, Full-Stack, E-Commerce)
    2. Quality Assurance (Testing & Automation)
    3. Cloud (DevOps, Infrastructure, Storage)
    4. Data (Data Engineering, Analytics)
    5. Artificial Intelligence (AI/ML, Gemini & AI Pro)
    """
    if project_name and project_name.strip() and project_name.strip() not in (GENERIC_PROJECT_TITLE, "Project Proposal", "Proposal"):
        return project_name.strip()

    if requirements is None:
        return "Software Services — Full-Stack Web Application"

    platforms = [p.lower() for p in (requirements.target_platform or [])]
    features = [f.lower() for f in (requirements.features or [])]
    stack = [s.lower() for s in (requirements.tech_stack or [])]
    # **The structured fields only.** This used to append the entire brief
    # markdown, which made the title depend on prose the estimate never saw: one
    # "model" or "store" anywhere in a 20-page RFP re-categorised the whole
    # engagement, and hop 2's job is precisely to decide which of those words
    # were requirements. `source_markdown_path` stays on the contract — the
    # currency detector below still reads it, deliberately, because a currency
    # *is* stated in prose.
    all_text = " ".join(platforms + features + stack).lower()

    # Whole-word matching, because these keys are short. `"ai" in all_text` was
    # true for any document containing "email", "domain", "maintain", "detail",
    # "available", "chain" or "main" — and since has_ai is tested first,
    # essentially every proposal was titled "Artificial Intelligence — AI/ML
    # Engineering & Model Solutions". Measured: features of ["Plain contact
    # form"] produced exactly that.
    def has(*keys: str) -> bool:
        """True when any key appears in ``all_text`` as a whole word/phrase."""
        return any(_word_in(k, all_text) for k in keys)

    # "model" and "data" are gone as standalone keys: both are ordinary
    # procurement boilerplate ("operating model", "governance model", "master
    # data"), and has_ai is tested first, so a WordPress brochure site was still
    # headed as an AI/ML engagement. The compound forms carry the real signal.
    has_ai = has("ai", "ml", "machine learning", "llm", "rag", "gemini", "ai pro")
    has_cloud = has("cloud", "devops", "infrastructure", "sre", "aws", "gcp", "azure", "kubernetes", "docker")
    has_data = has("data engineering", "data pipeline", "data warehouse", "analytics", "pipeline", "etl", "data science")
    has_qa = has("qa", "testing", "automation test", "quality assurance") and not has("web", "app", "mobile")

    has_web = has("web", "website", "web app", "webapp", "portal", "browser", "dashboard", "frontend", "fullstack", "backend")
    has_mobile = has("mobile", "ios", "android", "app store", "flutter", "react native")
    has_ecommerce = has("ecommerce", "e-commerce", "checkout", "store", "product catalog", "cart", "payment gateway")
    has_website_only = has("cms", "landing page", "corporate website", "marketing site", "wordpress") and not has("dashboard", "backend api", "microservice", "saas", "fullstack")

    # Handbook Category 5: Artificial Intelligence
    if has_ai:
        return "Artificial Intelligence — AI/ML Engineering & Model Solutions"

    # Handbook Category 4: Data
    if has_data and not (has_web or has_mobile):
        return "Data Services — Data Engineering & Analytics Platform"

    # Handbook Category 3: Cloud
    if has_cloud and not (has_web or has_mobile):
        return "Cloud Services — Cloud Architecture & DevOps Infrastructure"

    # Handbook Category 2: Quality Assurance
    if has_qa:
        return "Quality Assurance — Software Testing & Automation"

    # Handbook Category 1: Software Services
    if has_ecommerce:
        if has_mobile and has_web:
            return "Software Services — E-Commerce Web & Mobile Platform"
        else:
            return "Software Services — E-Commerce Web Application"

    if has_website_only:
        return "Software Services — Custom Website & CMS"

    if has_mobile and has_web:
        return "Software Services — Web & Mobile Application Platform"

    if has_mobile:
        return "Software Services — Mobile Application (iOS/Android)"

    if has_web:
        return "Software Services — Full-Stack Web Application"

    return "Software Services — Custom Software Development"


def _project_description(requirements: ExtractedRequirements | None) -> str | None:
    """A factual restatement of the brief, never a sales paragraph."""
    if requirements is None:
        return None
    parts: list[str] = []
    if requirements.target_platform:
        parts.append("Platforms: " + ", ".join(requirements.target_platform))
    if requirements.features:
        parts.append("Scope: " + ", ".join(requirements.features))
    if requirements.tech_stack:
        parts.append("Stack: " + ", ".join(requirements.tech_stack))
    return ". ".join(parts)[:1200] or None


def _sentence(text: str) -> str:
    """One trailing period, whatever the source had.

    The list items come from an LLM and are inconsistently punctuated; joining
    them and appending a period produced 'analytics dashboard..' on the first
    live render.
    """
    return text.rstrip(" .") + "."


def _notes(requirements: ExtractedRequirements | None) -> str | None:
    """Constraints, extraction gaps, and — critically — unread pages."""
    if requirements is None:
        return None
    parts: list[str] = []
    if requirements.constraints:
        parts.append(_sentence("Stated constraints: " + "; ".join(requirements.constraints)))
    if requirements.pages_failed:
        pages = ", ".join(str(p) for p in requirements.pages_failed)
        parts.append(
            f"This quote was prepared from a brief whose page(s) {pages} could not "
            "be read. Scope and price may change once they are supplied."
        )
    elif requirements.pages_total and requirements.pages_parsed < requirements.pages_total:
        parts.append(
            f"Prepared from {requirements.pages_parsed} of {requirements.pages_total} "
            "pages of the supplied brief."
        )
    if requirements.extraction_notes:
        parts.append(_sentence("Assumptions: " + "; ".join(requirements.extraction_notes)))
    return " ".join(parts)[:1200] or None


def _detect_quote_currency(
    req: ExtractedRequirements | None,
    est: EstimationResult | None,
    raw_req: dict[str, Any] | None = None,
    raw_est: dict[str, Any] | None = None,
) -> tuple[str, str]:
    if req is not None and getattr(req, "currency_symbol", None) and req.currency_symbol != "$":
        return (req.currency_symbol, req.currency_code)

    if req is not None and req.source_markdown_path:
        try:
            path = Path(req.source_markdown_path)
            if path.exists() and path.is_file():
                source_text = path.read_text(encoding="utf-8")
                sym, code = detect_currency(source_text)
                if sym != "$" or code != "USD":
                    return (sym, code)
        except Exception:
            pass

    # Check requirements text first
    req_samples: list[str] = []
    if req:
        req_samples.extend(req.constraints)
        req_samples.extend(req.extraction_notes)
        req_samples.extend(req.features)
        req_samples.extend(req.tech_stack)
    if raw_req and isinstance(raw_req, dict):
        req_samples.append(str(raw_req))

    req_text = " ".join(req_samples)
    req_sym, req_code = detect_currency(req_text)
    if req_sym != "$" or req_code != "USD":
        return (req_sym, req_code)

    # Fallback to estimation text
    est_samples: list[str] = []
    if est:
        est_samples.append(est.summary)
    if raw_est and isinstance(raw_est, dict):
        est_samples.append(str(raw_est))

    return detect_currency(" ".join(req_samples + est_samples))


def build_quote_context(
    *,
    proposal_id: str,
    requirements: ExtractedRequirements | dict[str, Any] | None = None,
    estimation: EstimationResult | dict[str, Any] | None = None,
    client_name: str | None = None,
    project_name: str | None = None,
    today: date | None = None,
    tax_rate_percent: Decimal | str | None = None,
) -> ProposalQuoteContext:
    """Assemble the validated template context for one proposal.

    ``today`` is a parameter so tests are deterministic and so the quote date,
    the validity window, and the roadmap's phase dates are all derived from one
    instant rather than three separate clock reads. It defaults to the system
    date here, at the boundary — the schema and the store below it never read a
    clock.

    Raises:
        EmptyEstimate: the estimation carried no priced work.
    """
    today = today or date.today()
    est = _as_estimation(estimation)
    req = _as_requirements(requirements)

    raw_req_dict = requirements if isinstance(requirements, dict) else None
    raw_est_dict = estimation if isinstance(estimation, dict) else None
    currency_symbol, currency_code = _detect_quote_currency(req, est, raw_req_dict, raw_est_dict)

    items = _line_items(est, target_currency=currency_code)
    if not items:
        raise EmptyEstimate(
            "no priced work to quote: run estimate_cost_and_timeline first"
        )

    return ProposalQuoteContext(
        quote_number=quote_number_for(proposal_id, today),
        quote_date=today,
        valid_until=today + timedelta(days=config.quote_valid_days()),
        currency_symbol=currency_symbol,
        currency_code=currency_code,
        company_name=config.company_name(),
        company_subtitle="Digital Engineering & Cloud Transformation",
        company_email=config.company_email(),
        company_website=config.company_website(),
        logo_url=data_uri(config.logo_path()),
        # A blank name is the normal path, not an edge case: neither docparse
        # hop supplies one and the visitor may decline to give one.
        client_name=client_name or GENERIC_CLIENT_LABEL,
        project_title=infer_project_title(req, project_name),
        project_description=_project_description(req),
        line_items=items,
        tax_rate_percent=Decimal(
            str(tax_rate_percent if tax_rate_percent is not None else config.tax_rate_percent())
        ),
        milestones=_milestones(est, today),
        notes=_notes(req),
        payment_terms=config.payment_terms(),
    )
