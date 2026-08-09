"""Jinja environment and the ``render_quote_html`` seam.

**Autoescape is on and must stay on.** Half of what lands in this template is
document-derived — a client name suggested by ``docparse/names.py``, feature
strings an LLM read out of an attacker-controllable brief — and the output is
handed to a browser engine that executes what it is given. Autoescape is the
one thing standing between a planted ``<script>`` in an uploaded RFP and script
execution inside the renderer. ``StrictUndefined`` is the companion rule: a
template that renders an unknown name as an empty string turns a typo'd key
into a professional-looking quote with a blank column.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from stratpoint_rag.pdf_gen import filters
from stratpoint_rag.pdf_gen.schema import ProposalQuoteContext

__all__ = ["DEFAULT_TEMPLATE", "TEMPLATE_DIR", "get_environment", "render_quote_html"]

TEMPLATE_DIR = Path(__file__).parent / "templates"
DEFAULT_TEMPLATE = "quote-template-c.html"

_env: Environment | None = None


def get_environment() -> Environment:
    """The process-wide environment. Built once; templates are cached in it."""
    global _env
    if _env is None:
        env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=select_autoescape(("html", "htm", "xml")),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        filters.register(env)
        _env = env
    return _env


def render_quote_html(
    context: ProposalQuoteContext | dict,
    *,
    template_name: str = DEFAULT_TEMPLATE,
) -> str:
    """Validate ``context`` and render it to a standalone HTML string.

    A ``dict`` is validated into a ``ProposalQuoteContext`` first — the point of
    the seam is that nothing reaches the template unvalidated, so accepting a
    raw mapping straight through would defeat it.

    The context is passed as ``model_dump()`` rather than as the model, so the
    template's flat ``{{ company_name }}`` names resolve and every
    ``@computed_field`` (subtotal, tax, per-row totals) is materialised once
    instead of on every access. Python mode, not JSON: the template's
    ``date_format`` and ``currency_format`` filters want real ``date`` and
    ``Decimal`` objects.
    """
    model = (
        context
        if isinstance(context, ProposalQuoteContext)
        else ProposalQuoteContext.model_validate(context)
    )
    return get_environment().get_template(template_name).render(**model.model_dump())
