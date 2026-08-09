"""Proposal templating and PDF generation.

The pipeline, in the order the pieces run::

    agent contracts ──mapping.build_quote_context──> ProposalQuoteContext
                    ──templating.render_quote_html──> HTML string
                    ──pdf_service.generate_pdf_from_html──> data/proposals/<sid>/<pid>.pdf

Each arrow is a seam that can be exercised alone: the context without a
template, the HTML without a browser, the browser without the agent. That split
is why the whole thing is testable offline — only the last stage needs Chromium.

``agent/tools.py:generate_proposal_pdf`` is the only production caller; it
imports this package lazily so the agent stays importable on a machine where
``playwright install chromium`` has never been run.

Rendering is behind ``pdf_service`` for the same reason PyMuPDF is behind
``docparse/render.py``: swapping Chromium for another engine should be one
module's problem.
"""

from stratpoint_rag.pdf_gen.mapping import EmptyEstimate, build_quote_context
from stratpoint_rag.pdf_gen.pdf_service import (
    PdfOptions,
    PdfRenderError,
    agenerate_pdf_from_html,
    generate_pdf_from_html,
)
from stratpoint_rag.pdf_gen.schema import (
    LineItem,
    MilestoneItem,
    ProposalQuoteContext,
)
from stratpoint_rag.pdf_gen.templating import (
    DEFAULT_TEMPLATE,
    TEMPLATE_DIR,
    render_quote_html,
)

__all__ = [
    "DEFAULT_TEMPLATE",
    "TEMPLATE_DIR",
    "EmptyEstimate",
    "LineItem",
    "MilestoneItem",
    "PdfOptions",
    "PdfRenderError",
    "ProposalQuoteContext",
    "agenerate_pdf_from_html",
    "build_quote_context",
    "generate_pdf_from_html",
    "render_quote_html",
]
