"""Typed contracts for the proposal-generation tools.

Teammates implementing real tool logic should maintain these input/output
Pydantic contracts so the orchestrator loop and API layer remain unchanged.

Tools & Owners:
1. `extract_brief_requirements` (owner: docparse — hops 1 and 2, built)
2. `estimate_cost_and_timeline` (owner: scoping_calculator)
3. `generate_proposal_pdf` (teammate: pdf_gen)

**Two contract changes landed with docparse hop 2. Both are breaking:**

- ``ExtractedRequirements`` no longer carries ``client_name`` or
  ``project_name``, and is now defined in ``stratpoint_rag.docparse.schema``
  (re-exported here, so existing imports keep resolving). A required name field
  is an instruction to hallucinate one — the old stub literally defaulted to
  "Acme Innovations". It gained provenance fields instead:
  ``source_markdown_path``, ``pages_total``, ``pages_parsed``, ``pages_failed``,
  ``extraction_notes``. ``complexity`` is now a ``Literal["low","medium","high"]``.
- ``ProposalPDFInput.client_name`` / ``.project_name`` are therefore
  ``str | None``. **``pdf_gen`` must render a generic heading and build a
  filename when both are None** — that is now the *normal* path, not an edge
  case, because declining to give a name is an offered choice (see
  ``disambiguation/engagement.py``).

A visitor-supplied name arrives on ``ProposalPDFInput``, never on
``ExtractedRequirements``: that model is the parser's statement about what the
*document* contained, and merging a human-typed value into it would make
"the brief said it" and "the visitor typed it" indistinguishable.
"""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field

# The hop-2 output contract lives in the package that produces it. Re-exported
# here so `from stratpoint_rag.agent.contracts import ExtractedRequirements`
# keeps working; the dependency runs agent -> docparse and must not be inverted.
from stratpoint_rag.docparse.schema import ExtractedRequirements


# ── 1. Brief Extraction Tool Contracts ──────────────────────────────────────


class BriefExtractionInput(BaseModel):
    """Input payload for extracting requirements from an uploaded client brief.

    **An id, never a path.** A path here would be LLM-generated free text
    flowing into ``open()``, and ``guardrails`` guards the visitor's *message*,
    not tool arguments. The id is resolved against the caller's session before
    anything is read.
    """

    upload_id: str = Field(
        ..., description="Opaque id of an upload from the attachment list."
    )


# ── 2. Proposal Calculator Tool Contracts ───────────────────────────────────────


class EstimationInput(BaseModel):
    """Input payload for estimating cost and timeline."""

    features: list[str] = Field(
        ..., description="List of feature requirements to scope."
    )
    target_platform: list[str] = Field(
        default_factory=lambda: ["Web"],
        description="Target platforms (e.g. Web, iOS, Android).",
    )
    complexity: str = Field(
        "medium", description="Project complexity (low, medium, high)."
    )
    complexity_weights: dict[str, float] | None = Field(
        None, description="Optional feature weight overrides for complexity."
    )
    role_rates: dict[str, float] | None = Field(
        None, description="Optional hourly rate overrides by role."
    )
    timeline_weeks: float | None = Field(
        None, description="Optional target project duration in weeks."
    )
    target_launch_date: str | None = Field(
        None, description="Optional target launch date or deadline."
    )
    custom_phases: list[PhaseTimelineItem] | list[dict[str, Any]] | None = Field(
        None, description="Optional dynamic LLM-generated phase roadmap items."
    )


class RoleBreakdownItem(BaseModel):
    """Cost & duration breakdown for a specific team role."""

    role: str = Field(..., description="Role title (e.g., Tech Lead, Senior Dev, QA).")
    estimated_hours: float = Field(..., description="Total estimated hours.")
    # Denominated in the parent EstimationResult.currency_code, not in USD.
    hourly_rate: float = Field(..., description="Hourly rate, in the estimate's currency.")
    total_cost: float = Field(..., description="Total cost for this role, same currency.")


class PhaseTimelineItem(BaseModel):
    """Timeline phase details."""

    phase_name: str = Field(..., description="Phase name (e.g. Discovery & Architecture).")
    duration_weeks: float = Field(..., description="Phase duration in weeks.")
    milestones: list[str] = Field(
        default_factory=list, description="Deliverables/milestones for this phase."
    )


class EstimationResult(BaseModel):
    """Output payload containing cost, timeline, and breakdown details.

    **The amounts are denominated in ``currency_code``, not necessarily USD.**
    The estimator prices from a PHP handbook and converts to whatever currency
    the brief was written in, so a peso brief yields peso amounts. Carrying the
    code is what stops the number being *relabelled* downstream: it used to be
    absent, so the one Observation handed to the ReAct loop read both "a total
    investment of PHP 1,379,994.00" and "Total Cost: $1,379,994.00 USD" — the
    same figure under two currencies 60x apart, and the loop is instructed to
    quote that figure to the visitor.

    ``total_cost_usd`` keeps its name only so existing callers and stored
    payloads keep resolving; read it together with ``currency_code``, never as
    dollars. Renaming it is a worthwhile follow-up, not part of this fix.

    **``currency_code`` is optional and defaults to None, not to "USD".** The
    estimator always sets it, but the ReAct loop routinely re-supplies an
    estimation as a *dict* copied out of a prior Observation — and that dict
    predates the field, so it arrives without one. Under a "USD" default the
    peso amounts inside it were relabelled dollars and multiplied by sixty on
    the way to a peso quote: a PHP 2,987.00/hr rate printed as PHP 179,220.00/hr
    and a ~PHP 331,000 engagement as PHP 17,742,780.00. None means *undeclared*,
    which ``pdf_gen.mapping`` infers from the payload's own amounts and summary;
    it does not mean dollars. Making the field required is not the fix — it
    would raise a ValidationError on exactly the re-supply path the capture-sink
    fallback exists to serve, and the loop cannot tell a schema error from a
    real one.
    """

    total_cost_usd: float = Field(
        ..., description="Total estimated cost, denominated in currency_code."
    )
    currency_code: str | None = Field(
        None,
        description="ISO code the amounts are in ('USD' or 'PHP'). Omit only if "
        "genuinely unknown — it is then inferred, never assumed to be USD.",
    )
    estimated_weeks: float = Field(..., description="Total project timeline in weeks.")
    role_breakdown: list[RoleBreakdownItem] = Field(
        default_factory=list, description="Role-by-role cost breakdown."
    )
    phase_timeline: list[PhaseTimelineItem] = Field(
        default_factory=list, description="Phase-by-phase project roadmap."
    )
    summary: str = Field(..., description="Executive text summary of cost & timeline.")


# ── 3. PDF Generation Tool Contracts ─────────────────────────────────────────


class ProposalPDFInput(BaseModel):
    """Input payload for generating the final proposal PDF."""

    # None is the normal case, not an edge case: neither hop supplies these, and
    # the visitor is free to decline when asked. pdf_gen must build a filename
    # and render a heading without them.
    client_name: str | None = Field(
        None, description="Client company name, if the visitor supplied one."
    )
    project_name: str | None = Field(
        None, description="Project title, if the visitor supplied one."
    )
    # Optional since the real PDF pipeline landed. The ReAct loop hands this
    # tool free text, and the model routinely re-calls it having forgotten what
    # the estimator returned two turns ago; `generate_proposal_pdf` falls back
    # to the turn's capture sink, which holds what actually ran. Requiring them
    # raised a ValidationError at exactly the moment the fallback exists for —
    # and the loop cannot tell a schema error from a real one, so it retried
    # the same call verbatim until the repeat guard stopped it.
    requirements: ExtractedRequirements | dict[str, Any] | None = Field(
        None, description="Extracted requirements or features dictionary."
    )
    estimation: EstimationResult | dict[str, Any] | None = Field(
        None, description="Scope estimation result dictionary."
    )
    output_path: str | None = Field(
        None, description="Optional target file path for saving the PDF."
    )


class PDFGenerationResult(BaseModel):
    """Output payload for the generated proposal PDF."""

    pdf_path: str = Field(..., description="Path to the generated proposal PDF file.")
    file_size_bytes: int = Field(..., description="File size in bytes.")
    download_url: str = Field(..., description="Download URL or relative link.")
    status: str = Field("success", description="Generation status (e.g. 'success').")


EstimationInput.model_rebuild()
RoleBreakdownItem.model_rebuild()
PhaseTimelineItem.model_rebuild()
EstimationResult.model_rebuild()
ProposalPDFInput.model_rebuild()
PDFGenerationResult.model_rebuild()

