"""Agent tools: corpus-grounded Q&A, downloadable-resource lookup,
and proposal-generation stubs (CV brief parsing, proposal calculator, PDF generation).

All tools are plain callables. Tool selection is driven by the `description` on
their ToolSpec below, which is rendered into the ReAct loop's system prompt.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Callable

from stratpoint_rag.agent.contracts import (
    BriefParserInput,
    EstimationInput,
    EstimationResult,
    ExtractedRequirements,
    PDFGenerationResult,
    PhaseTimelineItem,
    ProposalPDFInput,
    RoleBreakdownItem,
)
from stratpoint_rag.agent.models import ProposalData
from stratpoint_rag.rag.answer import answer_grounded as _rag_answer_grounded
from stratpoint_rag.rag.retrieve import retrieve as _retrieve

log = logging.getLogger(__name__)

# ── Per-invocation capture ────────────────────────────────────────────────
_chunk_sink: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "agent_chunk_sink", default=None
)
_grounded_sink: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "agent_grounded_sink", default=None
)
_proposal_sink: contextvars.ContextVar[ProposalData | None] = contextvars.ContextVar(
    "agent_proposal_sink", default=None
)


def begin_capture() -> None:
    """Start capturing tool-retrieved chunks, grounded metadata, and proposal data."""
    _chunk_sink.set([])
    _grounded_sink.set([])
    _proposal_sink.set(ProposalData())


def end_capture() -> None:
    """Stop capturing and reset sinks."""
    _chunk_sink.set(None)
    _grounded_sink.set(None)
    _proposal_sink.set(None)


def captured_chunks() -> list:
    """Chunks retrieved since begin_capture()."""
    return _chunk_sink.get() or []


def captured_grounded() -> list:
    """GroundedAnswer objects produced since begin_capture()."""
    return _grounded_sink.get() or []


def captured_proposal_data() -> ProposalData | None:
    """ProposalData collected since begin_capture()."""
    return _proposal_sink.get()


def _record_chunks(chunks) -> None:
    sink = _chunk_sink.get()
    if sink is not None and chunks:
        sink.extend(chunks)


def _record_grounded(grounded) -> None:
    sink = _grounded_sink.get()
    if sink is not None and grounded is not None:
        sink.append(grounded)


def _update_proposal_data(**kwargs) -> None:
    sink = _proposal_sink.get()
    if sink is not None:
        for k, v in kwargs.items():
            if hasattr(sink, k) and v is not None:
                setattr(sink, k, v)


# Markdown links regex for PDF documents
_DOC_LINK = re.compile(
    r"\[([^\]]+)\]\((https?://[^\s)]+?\.pdf(?:\?[^\s)]*)?)\)",
    re.IGNORECASE,
)


def _extract_doc_links(text: str) -> list[tuple[str, str]]:
    """Return [(title, url)] for downloadable-doc links in markdown text (deduped)."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for label, url in _DOC_LINK.findall(text or ""):
        if url in seen:
            continue
        seen.add(url)
        title = label.strip(" *_") or url
        out.append((title, url))
    return out


# ── Existing Chatbot Tools ───────────────────────────────────────────────


def search_stratpoint(query: str) -> str:
    """Answer a question about Stratpoint using the company's website content.
    Use for anything about Stratpoint's services, company, case studies, or blog.

    Args:
        query: The visitor's question, e.g. 'Do you offer cloud migration?'
    """
    try:
        text, chunks, grounded, _ = _rag_answer_grounded(query)
        _record_chunks(chunks)
        _record_grounded(grounded)
        return text
    except Exception as ex:  # surfaced as an Observation so the loop can recover
        return f"search_stratpoint error: {type(ex).__name__}: {ex}"


def find_resource(topic: str) -> str:
    """Find downloadable resources (PDFs/whitepapers) related to a topic, drawn
    from Stratpoint's website content.

    Args:
        topic: The full, specific subject to find resources for.
    """
    try:
        chunks = _retrieve(topic, k=10)
        _record_chunks(chunks)
    except Exception as ex:
        return f"find_resource error: {type(ex).__name__}: {ex}"

    seen: set[str] = set()
    links: list[tuple[str, str]] = []
    for c in chunks:
        for title, url in _extract_doc_links(c.text):
            if url in seen:
                continue
            seen.add(url)
            links.append((title, url))

    if not links:
        return f"No downloadable resources found for '{topic}'."
    lines = "\n".join(f"- {t} ({u})" for t, u in links)
    return f"Downloadable resources for '{topic}':\n{lines}"


# ── Proposal Tools (Stubs & Typed Interfaces) ────────────────────────────


def _parse_input_dict_or_str(input_data: Any) -> dict[str, Any]:
    """Helper to deserialize string/JSON/dict inputs safely."""
    if isinstance(input_data, dict):
        return input_data
    if isinstance(input_data, str):
        s = input_data.strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                pass
        return {"file_path": s, "query": s, "features": [s]}
    return {}


def parse_client_brief(input_data: BriefParserInput | str | dict[str, Any]) -> ExtractedRequirements:
    """Extract project requirements, target platform, features, and constraints from an uploaded client brief PDF or image.

    # TODO(teammate - cv_parser): replace stub marker with real CV/PDF parsing implementation

    Args:
        input_data: File path string or BriefParserInput model/dict.

    Returns:
        ExtractedRequirements Pydantic model containing client name, features, constraints, target platforms, and tech stack.
    """
    if isinstance(input_data, BriefParserInput):
        payload = input_data
    elif isinstance(input_data, dict):
        payload = BriefParserInput.model_validate(input_data)
    else:
        d = _parse_input_dict_or_str(input_data)
        file_path = d.get("file_path", str(input_data))
        client_name = d.get("client_name")
        payload = BriefParserInput(file_path=file_path, client_name=client_name)

    # Mock stub implementation returning realistic fake data
    client_name = payload.client_name or "Acme Innovations"
    file_name = os.path.basename(payload.file_path)

    result = ExtractedRequirements(
        client_name=client_name,
        project_name=f"Digital Commerce Platform ({file_name})",
        target_platform=["Web App", "iOS", "Android"],
        features=[
            "User Authentication & SSO",
            "Product Catalog & Multi-Filter Search",
            "Shopping Cart & Checkout Flow",
            "Payment Gateway Integration",
            "Real-Time Order Tracking",
            "Customer Analytics Dashboard",
        ],
        constraints=[
            "Target launch in 12 weeks",
            "SOC2 & GDPR compliance required",
            "High concurrency support for promotional events",
        ],
        tech_stack=["Python", "FastAPI", "React", "Flutter", "PostgreSQL", "AWS"],
        complexity="high",
    )
    _update_proposal_data(requirements=result)
    return result


def estimate_cost_and_timeline(input_data: EstimationInput | str | dict[str, Any]) -> EstimationResult:
    """Compute estimated project cost (USD), timeline in weeks, role breakdown, and phase roadmap from extracted requirements.

    # TODO(teammate - scoping_calculator): replace stub body with real calculator implementation (hardcoded rules for website complexity, QA managers, dev roles)

    Args:
        input_data: Scope details or EstimationInput model/dict.

    Returns:
        EstimationResult Pydantic model containing total cost, timeline in weeks, role breakdown, and phase roadmap.
    """
    # ==========================================================================
    # SKELETON STUB IMPLEMENTATION FOR SCOPING CALCULATOR
    # ==========================================================================
    # Teammate Implementation Seam:
    # Replace the skeleton stub logic below with your calculator rules when ready.
    #
    # Example Teammate Calculator Logic (Draft / Placeholder):
    #   if payload.complexity in ("simple", "low"):
    #       qa_managers = 1
    #       devs = 2
    #       weeks = 4.0
    #   elif payload.complexity in ("complex", "high"):
    #       qa_managers = 2
    #       devs = 4
    #       weeks = 12.0
    # ==========================================================================

    if isinstance(input_data, EstimationInput):
        payload = input_data
    elif isinstance(input_data, dict):
        payload = EstimationInput.model_validate(input_data)
    else:
        d = _parse_input_dict_or_str(input_data)
        features = d.get("features", ["User Authentication", "Product Catalog", "Payment Gateway"])
        if isinstance(features, str):
            features = [features]
        payload = EstimationInput(
            features=features,
            target_platform=d.get("target_platform", ["Web", "Mobile"]),
            complexity=d.get("complexity", "medium"),
        )

    # Skeleton placeholder calculation (returns realistic stub data so agent loop remains runnable)
    num_features = max(1, len(payload.features))
    complexity_mult = 1.2 if payload.complexity in ("high", "complex") else (0.8 if payload.complexity in ("low", "simple") else 1.0)
    weeks = round((4.0 + num_features * 1.3) * complexity_mult, 1)

    roles = [
        RoleBreakdownItem(
            role="Tech Lead / Solutions Architect",
            estimated_hours=weeks * 15,
            hourly_rate=100.0,
            total_cost=weeks * 15 * 100.0,
        ),
        RoleBreakdownItem(
            role="Senior Fullstack Engineer",
            estimated_hours=weeks * 30,
            hourly_rate=75.0,
            total_cost=weeks * 30 * 75.0,
        ),
        RoleBreakdownItem(
            role="QA Automation Manager",
            estimated_hours=weeks * 15,
            hourly_rate=50.0,
            total_cost=weeks * 15 * 50.0,
        ),
        RoleBreakdownItem(
            role="UI/UX Designer",
            estimated_hours=weeks * 10,
            hourly_rate=60.0,
            total_cost=weeks * 10 * 60.0,
        ),
    ]

    total_cost = sum(r.total_cost for r in roles)

    phases = [
        PhaseTimelineItem(
            phase_name="Phase 1: Discovery & System Architecture",
            duration_weeks=round(weeks * 0.2, 1),
            milestones=["Technical Architecture Document", "UI/UX Wireframes"],
        ),
        PhaseTimelineItem(
            phase_name="Phase 2: Core Development & Integration",
            duration_weeks=round(weeks * 0.5, 1),
            milestones=["Feature Implementation", "API Integrations", "Database Setup"],
        ),
        PhaseTimelineItem(
            phase_name="Phase 3: QA, Security & Deployment",
            duration_weeks=round(weeks * 0.3, 1),
            milestones=["Security Audit & GDPR Compliance", "User Acceptance Testing", "Production Launch"],
        ),
    ]

    result = EstimationResult(
        total_cost_usd=total_cost,
        estimated_weeks=weeks,
        role_breakdown=roles,
        phase_timeline=phases,
        summary=f"Skeleton Stub Estimate: {weeks} weeks duration for a total investment of ${total_cost:,.2f} USD.",
    )
    _update_proposal_data(estimation=result)
    return result


def generate_proposal_pdf(input_data: ProposalPDFInput | str | dict[str, Any]) -> PDFGenerationResult:
    """Assemble and render the final branded PDF project proposal complete with scope, cost, timeline, and deliverables.

    # TODO(teammate - pdf_gen): replace stub marker with real PDF generation implementation

    Args:
        input_data: Proposal content or ProposalPDFInput model/dict.

    Returns:
        PDFGenerationResult Pydantic model containing the generated PDF file path, size, and download URL.
    """
    if isinstance(input_data, ProposalPDFInput):
        payload = input_data
    elif isinstance(input_data, dict):
        payload = ProposalPDFInput.model_validate(input_data)
    else:
        d = _parse_input_dict_or_str(input_data)
        payload = ProposalPDFInput(
            client_name=d.get("client_name", "Acme Innovations"),
            project_name=d.get("project_name", "Digital Commerce Platform"),
            requirements=d.get("requirements", {"features": ["Authentication", "Checkout"]}),
            estimation=d.get("estimation", {"total_cost_usd": 45000.0, "estimated_weeks": 12.0}),
        )

    clean_client = re.sub(r"[^\w]+", "_", payload.client_name.lower()).strip("_")
    output_dir = payload.output_path or os.path.join(".", "data", "proposals")
    os.makedirs(os.path.dirname(output_dir) if output_dir.endswith(".pdf") else output_dir, exist_ok=True)

    pdf_path = (
        output_dir
        if output_dir.endswith(".pdf")
        else os.path.join(output_dir, f"stratpoint_proposal_{clean_client}.pdf")
    )

    try:
        with open(pdf_path, "w", encoding="utf-8") as f:
            f.write(f"%PDF-1.4 Mock Proposal for {payload.client_name} - {payload.project_name}\n")
    except Exception as ex:
        log.warning("Could not write stub PDF file to %s: %s", pdf_path, ex)

    result = PDFGenerationResult(
        pdf_path=pdf_path,
        file_size_bytes=1048576,
        download_url=f"/data/proposals/stratpoint_proposal_{clean_client}.pdf",
        status="success",
    )
    _update_proposal_data(pdf=result)
    return result


# ── String observation formatters for ReAct loop ──────────────────────────


def _wrap_parse_client_brief(raw_input: str) -> str:
    res = parse_client_brief(raw_input)
    return (
        f"Extracted Client Brief Requirements for '{res.client_name}':\n"
        f"- Project: {res.project_name}\n"
        f"- Target Platforms: {', '.join(res.target_platform)}\n"
        f"- Features: {', '.join(res.features)}\n"
        f"- Constraints: {', '.join(res.constraints)}\n"
        f"- Tech Stack: {', '.join(res.tech_stack)}\n"
        f"- Complexity: {res.complexity}"
    )


def _wrap_estimate_cost_and_timeline(raw_input: str) -> str:
    res = estimate_cost_and_timeline(raw_input)
    roles = ", ".join(f"{r.role} (${r.total_cost:,.0f})" for r in res.role_breakdown)
    return (
        f"Estimation Results:\n"
        f"- Summary: {res.summary}\n"
        f"- Duration: {res.estimated_weeks} weeks\n"
        f"- Total Cost: ${res.total_cost_usd:,.2f} USD\n"
        f"- Role Breakdown: {roles}"
    )


def _wrap_generate_proposal_pdf(raw_input: str) -> str:
    res = generate_proposal_pdf(raw_input)
    return (
        f"Proposal PDF Generated Successfully:\n"
        f"- PDF Path: {res.pdf_path}\n"
        f"- File Size: {res.file_size_bytes} bytes\n"
        f"- Download URL: {res.download_url}"
    )


# ── Dataclass for Tool Specs ─────────────────────────────────────────────


@dataclass(frozen=True)
class ToolSpec:
    """One tool the ReAct loop may call."""

    name: str
    fn: Callable[[str], str]
    arg: str
    description: str


TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="search_stratpoint",
        fn=search_stratpoint,
        arg="query",
        description=(
            "Answer any question about Stratpoint using the company's website "
            "content: the company, its people and leaders, services, case "
            "studies, or blog posts. This is the default tool. Input: the "
            "visitor's question, e.g. 'Do you offer cloud migration?'"
        ),
    ),
    ToolSpec(
        name="find_resource",
        fn=find_resource,
        arg="topic",
        description=(
            "Find downloadable resources (PDFs, whitepapers, reports) on a "
            "topic. Use ONLY when the visitor asks for a document to read or "
            "download. Input: the visitor's FULL, specific wording, keeping "
            "their figures and years, e.g. 'how many business tasks will be "
            "automated by 2027'. Do NOT shorten it to broad keywords."
        ),
    ),
    ToolSpec(
        name="parse_client_brief",
        fn=_wrap_parse_client_brief,
        arg="file_path",
        description=(
            "Extract requirements, platform, features, and constraints from an "
            "uploaded client brief file. Input: file path of brief, e.g. 'brief.pdf'."
        ),
    ),
    ToolSpec(
        name="estimate_cost_and_timeline",
        fn=_wrap_estimate_cost_and_timeline,
        arg="scope_input",
        description=(
            "Estimate project timeline in weeks, total cost in USD, and role "
            "breakdown based on extracted features and complexity. Input: "
            "description of features/scope."
        ),
    ),
    ToolSpec(
        name="generate_proposal_pdf",
        fn=_wrap_generate_proposal_pdf,
        arg="proposal_details",
        description=(
            "Assemble and render the final branded PDF proposal document. Input: "
            "client name and project details."
        ),
    ),
]

TOOL_REGISTRY: dict[str, Callable[[str], str]] = {s.name: s.fn for s in TOOL_SPECS}
