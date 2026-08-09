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


class RoleBreakdownItem(BaseModel):
    """Cost & duration breakdown for a specific team role."""

    role: str = Field(..., description="Role title (e.g., Tech Lead, Senior Dev, QA).")
    estimated_hours: float = Field(..., description="Total estimated hours.")
    hourly_rate: float = Field(..., description="Hourly rate in USD.")
    total_cost: float = Field(..., description="Total cost in USD for this role.")


class PhaseTimelineItem(BaseModel):
    """Timeline phase details."""

    phase_name: str = Field(..., description="Phase name (e.g. Discovery & Architecture).")
    duration_weeks: float = Field(..., description="Phase duration in weeks.")
    milestones: list[str] = Field(
        default_factory=list, description="Deliverables/milestones for this phase."
    )


class EstimationResult(BaseModel):
    """Output payload containing cost, timeline, and breakdown details."""

    total_cost_usd: float = Field(..., description="Total estimated cost in USD.")
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
