"""Typed contracts for the proposal-generation tools.

Teammates implementing real tool logic should maintain these input/output
Pydantic contracts so the orchestrator loop and API layer remain unchanged.

Tools & Owners:
1. `parse_client_brief` (teammate: cv_parser)
2. `estimate_cost_and_timeline` (owner: scoping_calculator)
3. `generate_proposal_pdf` (teammate: pdf_gen)
"""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


# ── 1. CV Brief Parsing Tool Contracts ──────────────────────────────────────


class BriefParserInput(BaseModel):
    """Input payload for extracting requirements from a client brief PDF/image."""

    file_path: str = Field(
        ..., description="Path to the uploaded client brief PDF or image file."
    )
    client_name: str | None = Field(
        None, description="Optional override for client company name if known."
    )


class ExtractedRequirements(BaseModel):
    """Output payload containing extracted requirements, features, and constraints."""

    client_name: str = Field(..., description="Name of the client company.")
    project_name: str = Field(..., description="Title/name of the project.")
    target_platform: list[str] = Field(
        default_factory=list,
        description="Target platforms (e.g. Mobile iOS, Android, Web).",
    )
    features: list[str] = Field(
        default_factory=list, description="List of required project features."
    )
    constraints: list[str] = Field(
        default_factory=list,
        description="Project constraints (e.g. timeline, compliance, budget).",
    )
    tech_stack: list[str] = Field(
        default_factory=list,
        description="Suggested or required tech stack components.",
    )
    complexity: str = Field(
        "medium", description="Overall complexity assessment (low, medium, high)."
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

    client_name: str = Field(..., description="Client company name.")
    project_name: str = Field(..., description="Project title.")
    requirements: ExtractedRequirements | dict[str, Any] = Field(
        ..., description="Extracted requirements or features dictionary."
    )
    estimation: EstimationResult | dict[str, Any] = Field(
        ..., description="Scope estimation result dictionary."
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
