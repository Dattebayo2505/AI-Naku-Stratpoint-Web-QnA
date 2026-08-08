"""Agent tools: corpus-grounded Q&A, downloadable-resource lookup, client-brief
extraction, and the proposal calculator / PDF generation stubs.

All tools are plain callables. Tool selection is driven by the `description` on
their ToolSpec below, which is rendered into the ReAct loop's system prompt.

**The spec list is built per request, not imported.** `build_tool_specs()` is
the entry point; `TOOL_SPECS` / `TOOL_REGISTRY` are the no-attachment default
kept for callers that have no request context. Two tools depend on what the
request carries:

- `extract_brief_requirements` is registered **only when a brief is attached**.
  A tool that cannot succeed should not be offered: with nothing attached the
  model calls it anyway and gets an error Observation it then has to reason
  around. It also needs to close over the resolved uploads, because the loop
  dispatches `Callable[[str], str]` and the session id never reaches the model.
- `generate_proposal_pdf` closes over the visitor-supplied client/project name,
  which lives in session state and is deliberately absent from
  `ExtractedRequirements` (see `agent/contracts.py`).
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from stratpoint_rag.agent.contracts import (
    BriefExtractionInput,
    EstimationInput,
    EstimationResult,
    ExtractedRequirements,
    PDFGenerationResult,
    PhaseTimelineItem,
    ProposalPDFInput,
    RoleBreakdownItem,
)
from stratpoint_rag.agent.models import ProposalData
from stratpoint_rag.docparse import BriefRef, extract_brief
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


def _resolve_upload_id(raw: Any, briefs: list[BriefRef]) -> BriefRef | None:
    """Match whatever the model typed against the attached uploads.

    The manifest gives it the exact id, but a ReAct loop on an 8B model also
    types the filename, quotes the id, or wraps it in JSON. Falling back to the
    filename — and, when exactly one brief is attached, to that brief — turns a
    near-miss into a working turn instead of an error Observation the model then
    has to reason around. Nothing here reaches the filesystem: the value only
    ever selects from uploads already resolved for *this* session.
    """
    if not briefs:
        return None

    if isinstance(raw, BriefExtractionInput):
        wanted = raw.upload_id
    elif isinstance(raw, dict):
        wanted = str(raw.get("upload_id") or raw.get("id") or "")
    else:
        d = _parse_input_dict_or_str(raw)
        wanted = str(d.get("upload_id") or d.get("id") or raw or "")

    wanted = wanted.strip().strip("'\"").strip()
    if wanted:
        for brief in briefs:
            if brief.upload_id == wanted:
                return brief
        lowered = wanted.casefold()
        for brief in briefs:
            if brief.filename.casefold() == lowered or lowered in brief.upload_id:
                return brief

    return briefs[0] if len(briefs) == 1 else None


def extract_brief_requirements(
    input_data: BriefExtractionInput | str | dict[str, Any],
    briefs: list[BriefRef] | None = None,
) -> ExtractedRequirements:
    """Extract structured requirements from an already-transcribed client brief.

    This is docparse hop 2. Hop 1 already ran, eagerly, at upload time — this
    tool never opens a PDF, an image, or the vision model; it reads the Markdown
    artifact hop 1 wrote and turns it into a validated `ExtractedRequirements`.

    Args:
        input_data: The upload id from the attachment manifest.
        briefs: Uploads resolved for this session, bound per request.

    Returns:
        ExtractedRequirements with hop 1's page provenance copied through.
    """
    brief = _resolve_upload_id(input_data, briefs or [])
    if brief is None:
        raise ValueError(
            "No attached brief matches that id. Use an id from the attachment list."
        )

    result = extract_brief(brief)
    _update_proposal_data(requirements=result)
    return result


# Hop-1 transcriptions run to tens of thousands of characters; the loop resends
# every Observation on each subsequent turn, so an unbounded read would blow the
# context window several turns before the model answers.
BRIEF_EXCERPT_CHARS = 6000


def read_brief(
    input_data: str | dict[str, Any],
    briefs: list[BriefRef] | None = None,
) -> str:
    """Read what an uploaded brief actually says, in the document's own words.

    The counterpart to `extract_brief_requirements`, and not a duplicate of it.
    That tool returns `ExtractedRequirements` — platforms, features,
    constraints, complexity — which are *scoping* fields. Nothing in that shape
    can answer "what is this document about", so a visitor who asked was served
    a feature list or, worse, a full quote. This returns hop 1's Markdown, which
    is the only artifact that carries the document's prose.

    Truncation is stated, never silent — the same rule hop 2 follows. An excerpt
    presented as the whole document is how "I only read the first few pages"
    becomes an unqualified summary.

    Args:
        input_data: The upload id from the attachment manifest.
        briefs: Uploads resolved for this session, bound per request.

    Returns:
        The transcription text, prefixed with its page accounting.
    """
    brief = _resolve_upload_id(input_data, briefs or [])
    if brief is None:
        raise ValueError(
            "No attached brief matches that id. Use an id from the attachment list."
        )
    if not brief.transcribed:
        return f"'{brief.filename}' has not been transcribed yet."

    try:
        text = Path(brief.markdown_path).read_text(encoding="utf-8")
    except OSError as ex:
        return f"Could not read '{brief.filename}': {type(ex).__name__}."

    header = [f"Contents of '{brief.filename}'"]
    if brief.pages_total:
        header.append(f"({brief.pages_parsed} of {brief.pages_total} pages readable)")
    if brief.pages_failed:
        failed = ", ".join(str(n) for n in brief.pages_failed)
        header.append(f"- pages that could NOT be read: {failed}")

    body = text[:BRIEF_EXCERPT_CHARS]
    if len(text) > BRIEF_EXCERPT_CHARS:
        body += (
            f"\n\n[truncated here: this is the first {BRIEF_EXCERPT_CHARS} "
            "characters of a longer document. Say so if you summarize it.]"
        )
    return f"{' '.join(header)}:\n\n{body}"


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


# Used when no name was supplied. A neutral slug, never an invented company.
_UNNAMED_CLIENT_SLUG = "client"


def _client_slug(client_name: str | None) -> str:
    """Filename component for a proposal.

    ``client_name`` is ``str | None`` and None is the *normal* case: neither
    docparse hop supplies a name, and the visitor may decline to give one. The
    old code called ``.lower()`` on it unguarded, which turned "the visitor
    declined" into an AttributeError at the last step of the proposal chain.
    """
    slug = re.sub(r"[^\w]+", "_", (client_name or "").lower()).strip("_")
    return slug or _UNNAMED_CLIENT_SLUG


def generate_proposal_pdf(
    input_data: ProposalPDFInput | str | dict[str, Any],
    names: tuple[str | None, str | None] = (None, None),
) -> PDFGenerationResult:
    """Assemble and render the final branded PDF project proposal complete with scope, cost, timeline, and deliverables.

    # TODO(teammate - pdf_gen): replace stub marker with real PDF generation implementation

    Args:
        input_data: Proposal content or ProposalPDFInput model/dict.
        names: ``(client_name, project_name)`` the *visitor* supplied this
            session, bound per request. Used only to fill gaps — an explicit
            value in ``input_data`` wins. Both may be None; the proposal then
            carries a generic heading, which is a valid outcome, not a failure.

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
            # No invented fallbacks. "Acme Innovations" on a real quote is the
            # same hallucination the schema change removed.
            client_name=d.get("client_name"),
            project_name=d.get("project_name"),
            requirements=d.get("requirements", {}),
            estimation=d.get("estimation", {}),
            output_path=d.get("output_path"),
        )

    session_client, session_project = names
    client_name = payload.client_name or session_client
    project_name = payload.project_name or session_project

    clean_client = _client_slug(client_name)
    output_dir = payload.output_path or os.path.join(".", "data", "proposals")
    os.makedirs(os.path.dirname(output_dir) if output_dir.endswith(".pdf") else output_dir, exist_ok=True)

    pdf_path = (
        output_dir
        if output_dir.endswith(".pdf")
        else os.path.join(output_dir, f"stratpoint_proposal_{clean_client}.pdf")
    )

    heading = " - ".join(p for p in (client_name, project_name) if p) or "Project Proposal"
    try:
        with open(pdf_path, "w", encoding="utf-8") as f:
            f.write(f"%PDF-1.4 Mock Proposal for {heading}\n")
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


def _or_none(values: list[str]) -> str:
    return ", ".join(values) if values else "(none stated)"


def _format_requirements(res: ExtractedRequirements) -> str:
    """Render an extraction as an Observation.

    No client or project name appears here — the model would repeat one back to
    the visitor as established fact, and neither hop supplies one. The page
    accounting does appear: a brief where hop 1 lost 6 of 20 pages must not read
    like a clean one.
    """
    lines = [
        "Extracted requirements from the uploaded brief:",
        f"- Target Platforms: {_or_none(res.target_platform)}",
        f"- Features: {_or_none(res.features)}",
        f"- Constraints: {_or_none(res.constraints)}",
        f"- Tech Stack: {_or_none(res.tech_stack)}",
        f"- Complexity: {res.complexity}",
    ]
    if res.pages_total:
        lines.append(f"- Pages read: {res.pages_parsed} of {res.pages_total}")
    if res.pages_failed:
        failed = ", ".join(str(n) for n in res.pages_failed)
        lines.append(f"- Pages that could NOT be read: {failed}")
    if res.extraction_notes:
        lines.append(f"- Notes: {'; '.join(res.extraction_notes)}")
    return "\n".join(lines)


def _wrap_read_brief(briefs: list[BriefRef]) -> Callable[[str], str]:
    """Bind the session's resolved uploads to the loop's `(str) -> str` shape."""

    def run(raw_input: str) -> str:
        return read_brief(raw_input, briefs)

    return run


def _wrap_extract_brief_requirements(briefs: list[BriefRef]) -> Callable[[str], str]:
    """Bind the session's resolved uploads to the loop's `(str) -> str` shape."""

    def run(raw_input: str) -> str:
        return _format_requirements(extract_brief_requirements(raw_input, briefs))

    return run


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


def _wrap_generate_proposal_pdf(
    names: tuple[str | None, str | None] = (None, None),
) -> Callable[[str], str]:
    def run(raw_input: str) -> str:
        res = generate_proposal_pdf(raw_input, names)
        return (
            f"Proposal PDF Generated Successfully:\n"
            f"- PDF Path: {res.pdf_path}\n"
            f"- File Size: {res.file_size_bytes} bytes\n"
            f"- Download URL: {res.download_url}"
        )

    return run


# ── Dataclass for Tool Specs ─────────────────────────────────────────────


@dataclass(frozen=True)
class ToolSpec:
    """One tool the ReAct loop may call."""

    name: str
    fn: Callable[[str], str]
    arg: str
    description: str


BRIEF_TOOL_NAME = "extract_brief_requirements"
READ_BRIEF_TOOL_NAME = "read_brief"

# The old name, `parse_client_brief`, was wrong in every particular and is kept
# here only as a reminder not to reintroduce it. Tool names are part of the
# prompt: "parse" implies the tool performs the parsing, which invites the model
# to call it *instead of* asking the visitor to upload something — and by the
# time the loop runs, hop 1 has already parsed the document.
# The two brief tools are described against each other on purpose. Given only
# the extraction tool, a model asked "what is this document about" calls it and
# answers with a feature list — the closest thing it was offered.
_READ_BRIEF_DESCRIPTION = (
    "Read what an uploaded document actually says, in its own words. Use this "
    "to answer questions ABOUT a document: what it is, what it says, who sent "
    "it, or to summarize or quote it. Input: the upload id from the attachment "
    "list, e.g. 'a3f9c2'."
)

_BRIEF_TOOL_DESCRIPTION = (
    "Extract structured requirements, target platforms, features, and "
    "constraints from a client brief the visitor has already uploaded. Use this "
    "when SCOPING the work — building an estimate or a proposal. To answer a "
    f"question about what the document says, use {READ_BRIEF_TOOL_NAME} "
    "instead. Input: the upload id from the attachment list, e.g. 'a3f9c2'."
)


def build_tool_specs(
    briefs: list[BriefRef] | None = None,
    names: tuple[str | None, str | None] = (None, None),
) -> list[ToolSpec]:
    """Build the tool list for one request.

    ``briefs`` are the uploads resolved for this session; ``names`` is the
    ``(client_name, project_name)`` the visitor supplied, if any. Both are
    request-scoped, which is why this is a function and not a module constant.
    """
    specs = [
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
    ]

    # Conditional: a tool that cannot succeed should not be offered.
    if briefs:
        specs.append(
            ToolSpec(
                name=READ_BRIEF_TOOL_NAME,
                fn=_wrap_read_brief(briefs),
                arg="upload_id",
                description=_READ_BRIEF_DESCRIPTION,
            )
        )
        specs.append(
            ToolSpec(
                name=BRIEF_TOOL_NAME,
                fn=_wrap_extract_brief_requirements(briefs),
                arg="upload_id",
                description=_BRIEF_TOOL_DESCRIPTION,
            )
        )

    specs.append(
        ToolSpec(
            name="estimate_cost_and_timeline",
            fn=_wrap_estimate_cost_and_timeline,
            arg="scope_input",
            description=(
                "Estimate project timeline in weeks, total cost in USD, and role "
                "breakdown based on extracted features and complexity. Input: "
                "description of features/scope."
            ),
        )
    )
    specs.append(
        ToolSpec(
            name="generate_proposal_pdf",
            fn=_wrap_generate_proposal_pdf(names),
            arg="proposal_details",
            description=(
                "Assemble and render the final branded PDF proposal document. "
                "Input: the scope and estimate to write up. Do NOT invent a "
                "client or project name; if one is needed it is supplied for you."
            ),
        )
    )
    return specs


def build_tool_registry(specs: list[ToolSpec]) -> dict[str, Callable[[str], str]]:
    return {s.name: s.fn for s in specs}


# The no-attachment default, for callers with no request context. The loop
# builds its own per request — see react.run_react.
TOOL_SPECS: list[ToolSpec] = build_tool_specs()

TOOL_REGISTRY: dict[str, Callable[[str], str]] = build_tool_registry(TOOL_SPECS)
