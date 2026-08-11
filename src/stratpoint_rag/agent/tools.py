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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from stratpoint_rag import llmops
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
from stratpoint_rag.currency_calculator import calculate_role_rate, get_category_costings
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

# **Capture is re-entrant, and it has to be.** `run_with_guardrails` opens a
# capture and then calls `run_react`, which opens its own and closes it in a
# `finally`. Both run in one context, so a non-counting `end_capture` reset the
# sinks to None *before* the outer caller read them — and its reads came back
# empty every time. Measured: a turn whose `search_stratpoint` really did record
# a chunk reported zero chunks and zero grounded results to the layer above.
#
# What that cost, all silently: the output hallucination check verified answers
# against no source at all, and `is_grounded`/`confidence` were never set on any
# agent turn, which also made the clarify-streak hand-off unreachable there.
# `proposal_data` escaped only because `_finish` reads it before the reset.
#
# Only the outermost begin/end touches the sinks. `end_capture` clamps at zero
# rather than going negative so an unpaired call stays a hard reset — tests use
# it exactly that way, to clear state between cases.
_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "agent_capture_depth", default=0
)


def begin_capture() -> None:
    """Start capturing tool-retrieved chunks, grounded metadata, and proposal data.

    Nestable: an inner `begin_capture`/`end_capture` pair leaves the outer
    caller's sinks intact.
    """
    if _depth.get() == 0:
        _chunk_sink.set([])
        _grounded_sink.set([])
        _proposal_sink.set(ProposalData())
    _depth.set(_depth.get() + 1)


def end_capture() -> None:
    """Close one capture scope; the sinks clear only when the outermost closes.

    An unpaired call (depth already 0) resets the sinks unconditionally.
    """
    depth = max(0, _depth.get() - 1)
    _depth.set(depth)
    if depth == 0:
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

# How much text to return around each `query` hit, and how many hits to show.
# The product stays under BRIEF_EXCERPT_CHARS so a search costs no more context
# than the plain read it replaces.
BRIEF_MATCH_WINDOW_CHARS = 1400
BRIEF_MAX_MATCHES = 4

_PAGE_HEADING = re.compile(r"^## Page (\d+)", re.MULTILINE)


def _brief_query(raw: Any) -> str:
    """Pull an explicit search term out of the model's Action Input, or ''.

    Deliberately NOT routed through `_parse_input_dict_or_str`: that helper maps
    a bare string onto *every* key it knows, including `query`, so the ordinary
    `Action Input: a3f9c2` would arrive here as a search for the literal id and
    match nothing. A query counts only when the model named the key.
    """
    if isinstance(raw, dict):
        d = raw
    elif isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            d = json.loads(raw.strip())
        except json.JSONDecodeError:
            return ""
        if not isinstance(d, dict):
            return ""
    else:
        return ""
    value = d.get("query") or d.get("search") or d.get("q") or ""
    return str(value).strip().strip("'\"").strip()


def _page_of(text: str, pos: int) -> str:
    """Label a window by the nearest `## Page N` heading above it.

    Provenance survives the slice: an excerpt the model cannot place in the
    document is one it will cite as simply "the brief".
    """
    last = None
    for m in _PAGE_HEADING.finditer(text, 0, pos + 1):
        last = m.group(1)
    return f"Page {last}" if last else "start of document"


def _search_excerpts(text: str, query: str) -> list[str]:
    """Windows around each case-insensitive hit, overlaps merged."""
    needle = query.casefold()
    hay = text.casefold()

    # (window_start, window_end, first_match_pos). The page label is taken from
    # the *match*, not the window start: a window opens a few hundred characters
    # early and so routinely straddles a `## Page N` heading, which would label
    # a page-4 clause "Page 3" and put a wrong page number in the citation.
    spans: list[list[int]] = []
    at = hay.find(needle)
    while at != -1 and len(spans) < BRIEF_MAX_MATCHES:
        # Bias the window forward: the answer to "what does 2.10 say" is the
        # text after the marker, not the clause before it.
        start = max(0, at - BRIEF_MATCH_WINDOW_CHARS // 4)
        end = min(len(text), at + BRIEF_MATCH_WINDOW_CHARS)
        if spans and start <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], end)
        else:
            spans.append([start, end, at])
        at = hay.find(needle, end)

    return [
        f"[{_page_of(text, hit)}, characters {s}-{e} of {len(text)}]\n{text[s:e].strip()}"
        for s, e, hit in spans
    ]


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

    **`query` is what makes the rest of the document reachable at all.** Without
    it this returned the first `BRIEF_EXCERPT_CHARS` and nothing else, and since
    the tool took only an id, the model had no way to express "show me more" —
    its retry was byte-identical and the loop's repeat guard correctly refused
    it. Everything past the cap was unreachable *by construction*. Measured: a
    21k-character RFP answered "point 2.10 is not mentioned in the available
    content" when 2.10 sat at character 7,863. A varying query also gives the
    loop the differing feedback it needs to make progress.

    Args:
        input_data: The upload id from the attachment manifest, optionally as
            ``{"upload_id": ..., "query": ...}`` to search rather than read the
            opening.
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

    query = _brief_query(input_data)
    if query:
        excerpts = _search_excerpts(text, query)
        if not excerpts:
            # Never fall back to the opening on a miss: the model reads whatever
            # it is handed as "the thing you asked for" and answers about page 1.
            return (
                f"{' '.join(header)}: no passage matching '{query}' was found "
                f"anywhere in this {len(text)}-character document. Tell the "
                "visitor it does not appear, or try different wording."
            )
        body = "\n\n---\n\n".join(excerpts)
        return (
            f"{' '.join(header)} — {len(excerpts)} passage(s) matching "
            f"'{query}', not the whole document:\n\n{body}"
        )

    body = text[:BRIEF_EXCERPT_CHARS]
    if len(text) > BRIEF_EXCERPT_CHARS:
        body += (
            f"\n\n[truncated here: this is the first {BRIEF_EXCERPT_CHARS} "
            f"characters of {len(text)}. To read any other part, call "
            f"{READ_BRIEF_TOOL_NAME} again with "
            '{"upload_id": "' + brief.upload_id + '", "query": "<words to find>"}. '
            "Say so if you summarize from this excerpt alone.]"
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

    # Detect target currency and tech stack hints from captured session requirements / input payload
    captured = _proposal_sink.get()
    target_currency = "USD"
    if captured and captured.requirements and captured.requirements.currency_code:
        target_currency = captured.requirements.currency_code

    tech_hints = list(payload.features) + list(payload.target_platform)

    num_features = max(1, len(payload.features))
    complexity_mult = 1.2 if payload.complexity in ("high", "complex") else (0.8 if payload.complexity in ("low", "simple") else 1.0)
    weeks = round((4.0 + num_features * 1.3) * complexity_mult, 1)

    # Handbook.md rate lookup per role adjusted by tech stack and target currency
    tech_lead_rate, _ = calculate_role_rate("Tech Lead / Solutions Architect", target_currency=target_currency, tech_stack_hints=tech_hints)
    engineer_rate, _ = calculate_role_rate("Senior Fullstack Engineer", target_currency=target_currency, tech_stack_hints=tech_hints)
    qa_rate, _ = calculate_role_rate("QA Automation Manager", target_currency=target_currency, tech_stack_hints=tech_hints)
    designer_rate, _ = calculate_role_rate("UI/UX Designer", target_currency=target_currency, tech_stack_hints=tech_hints)

    r1_rate = float(tech_lead_rate)
    r2_rate = float(engineer_rate)
    r3_rate = float(qa_rate)
    r4_rate = float(designer_rate)

    roles = [
        RoleBreakdownItem(
            role="Tech Lead / Solutions Architect",
            estimated_hours=weeks * 15,
            hourly_rate=r1_rate,
            total_cost=round(weeks * 15 * r1_rate, 2),
        ),
        RoleBreakdownItem(
            role="Senior Fullstack Engineer",
            estimated_hours=weeks * 30,
            hourly_rate=r2_rate,
            total_cost=round(weeks * 30 * r2_rate, 2),
        ),
        RoleBreakdownItem(
            role="QA Automation Manager",
            estimated_hours=weeks * 15,
            hourly_rate=r3_rate,
            total_cost=round(weeks * 15 * r3_rate, 2),
        ),
        RoleBreakdownItem(
            role="UI/UX Designer",
            estimated_hours=weeks * 10,
            hourly_rate=r4_rate,
            total_cost=round(weeks * 10 * r4_rate, 2),
        ),
    ]

    # Category-specific handbook costing additions (Cloud, AI/ML, Data, Security, Licenses)
    extra_costings = get_category_costings(
        features=payload.features,
        target_platform=payload.target_platform,
        weeks=weeks,
        target_currency=target_currency,
    )
    for c_item in extra_costings:
        roles.append(
            RoleBreakdownItem(
                role=c_item["role"],
                estimated_hours=c_item["estimated_hours"],
                hourly_rate=c_item["hourly_rate"],
                total_cost=c_item["total_cost"],
            )
        )

    total_cost = round(sum(r.total_cost for r in roles), 2)

    feature_str = ", ".join(payload.features[:3]) if payload.features else "Core System Features"
    phases = [
        PhaseTimelineItem(
            phase_name="Phase 1: Discovery & System Architecture",
            duration_weeks=round(weeks * 0.2, 1),
            milestones=["Technical Architecture Document", "UI/UX Wireframes & Component Specs"],
        ),
        PhaseTimelineItem(
            phase_name="Phase 2: Core Development & Integration",
            duration_weeks=round(weeks * 0.5, 1),
            milestones=[f"Sprint Deliverables ({feature_str})", "API Integrations & Database Schema"],
        ),
        PhaseTimelineItem(
            phase_name="Phase 3: QA, Security & Production Deployment",
            duration_weeks=round(weeks * 0.3, 1),
            milestones=["End-to-End QA Testing", "Security Audit & Performance Optimization", "Production Launch & Handoff"],
        ),
    ]

    result = EstimationResult(
        total_cost_usd=total_cost,
        # The amounts above are in target_currency. Recording which one it was is
        # what keeps every reader downstream — the Observation formatter, the
        # quote mapping — from having to guess it back from the magnitude.
        currency_code=target_currency,
        estimated_weeks=weeks,
        role_breakdown=roles,
        phase_timeline=phases,
        summary=f"Handbook-Based Estimate: {weeks} weeks duration for a total investment of {target_currency} {total_cost:,.2f}.",
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

    Delegates to ``pdf_gen.filters.slugify`` so the rule that builds a filename
    and the rule a template author gets via ``| slugify`` cannot drift apart.
    """
    from stratpoint_rag.pdf_gen.filters import slugify

    return slugify(client_name, fallback=_UNNAMED_CLIENT_SLUG)


def _resolve_proposal_paths(
    output_path: str | None, session_id: str | None, proposal_id: str, client_slug: str
) -> tuple[Path, Path | None, str]:
    """Decide where the PDF, its HTML twin, and the download URL live.

    Two shapes, and the caller's explicit ``output_path`` always wins:

    - explicit path (``.pdf`` or a directory) — scripts, tests, and anything
      driving the tool directly. No session dir is invented around it.
    - nothing — the session-scoped store, ``data/proposals/<sid>/<pid>.pdf``,
      which is what the download endpoint serves and the TTL sweep reaches.

    Returns ``(pdf_path, html_path_or_None, download_url)``. The HTML twin is
    only written under the store, where the API can serve it for the UI preview;
    beside an arbitrary caller-chosen path it would just be litter.
    """
    from stratpoint_rag.pdf_gen import store as pdf_store

    if output_path:
        out = Path(output_path)
        pdf_path = out if out.suffix.lower() == ".pdf" else out / f"stratpoint_proposal_{client_slug}.pdf"
        return pdf_path, None, pdf_path.as_posix()

    session = session_id if session_id and pdf_store.is_safe_id(session_id) else pdf_store.ANONYMOUS_SESSION
    return (
        pdf_store.proposal_path(session, proposal_id, ".pdf"),
        pdf_store.proposal_path(session, proposal_id, ".html"),
        pdf_store.download_url(session, proposal_id),
    )


def generate_proposal_pdf(
    input_data: ProposalPDFInput | str | dict[str, Any],
    names: tuple[str | None, str | None] = (None, None),
    session_id: str | None = None,
) -> PDFGenerationResult:
    """Assemble and render the final branded PDF project proposal complete with scope, cost, timeline, and deliverables.

    Runs the real ``pdf_gen`` pipeline: the two agent contracts are mapped to a
    validated ``ProposalQuoteContext``, rendered through Jinja, and printed by
    headless Chromium. ``pdf_gen`` is imported *inside* the function on purpose —
    it pulls in Playwright, and ``agent.tools`` must stay importable on a machine
    where ``playwright install chromium`` has never been run (every test that
    monkeypatches a tool imports this module).

    Failure is raised, not swallowed. The ReAct loop already turns a tool
    exception into an Observation the model can react to, whereas a
    ``status="failed"`` result with an empty path reads as a success everywhere
    downstream — including in the "here is your proposal" sentence the loop
    writes next.

    Args:
        input_data: Proposal content or ProposalPDFInput model/dict.
        names: ``(client_name, project_name)`` the *visitor* supplied this
            session, bound per request. Used only to fill gaps — an explicit
            value in ``input_data`` wins. Both may be None; the proposal then
            carries a generic heading, which is a valid outcome, not a failure.
        session_id: Scopes the stored proposal and its download URL. None puts
            it under the anonymous session, which the sweep still reaches.

    Returns:
        PDFGenerationResult Pydantic model containing the generated PDF file path, size, and download URL.

    Raises:
        RuntimeError: the estimate was empty, or the browser could not render.
    """
    from stratpoint_rag.pdf_gen import (
        EmptyEstimate,
        PdfRenderError,
        build_quote_context,
        generate_pdf_from_html,
        render_quote_html,
    )
    from stratpoint_rag.pdf_gen import store as pdf_store

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

    # The model is free to re-call this tool having forgotten what the estimator
    # returned two turns ago; the capture sink is the turn's memory of it, so an
    # empty payload field falls back to what actually ran rather than to nothing.
    captured = _proposal_sink.get()
    requirements = payload.requirements or (captured.requirements if captured else None)
    estimation = payload.estimation or (captured.estimation if captured else None)

    proposal_id = pdf_store.new_proposal_id()
    pdf_path, html_path, download_url = _resolve_proposal_paths(
        payload.output_path, session_id, proposal_id, _client_slug(client_name)
    )

    try:
        context = build_quote_context(
            proposal_id=proposal_id,
            requirements=requirements,
            estimation=estimation,
            client_name=client_name,
            project_name=project_name,
        )
    except EmptyEstimate as ex:
        raise RuntimeError(
            f"{ex}. A proposal cannot be rendered without priced work."
        ) from ex

    html = render_quote_html(context)
    try:
        generate_pdf_from_html(html, pdf_path)
    except PdfRenderError as ex:
        raise RuntimeError(f"the proposal could not be rendered: {ex}") from ex

    if html_path is not None:
        # The UI previews this: a PDF in a data: URI inside Streamlit's
        # sandboxed iframe is blocked by Chrome, so the preview shows the HTML
        # the PDF was printed from.
        html_path.write_text(html, encoding="utf-8")

    result = PDFGenerationResult(
        pdf_path=str(pdf_path),
        file_size_bytes=pdf_path.stat().st_size,
        download_url=download_url,
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
    """Render an estimate as an Observation.

    Every amount is labelled with the estimate's own currency code. Hardcoding
    "$...USD" here put a peso figure in front of the model as dollars, in the
    same Observation whose summary line said PHP — and the system prompt tells
    the loop to repeat that figure to the visitor.
    """
    res = estimate_cost_and_timeline(raw_input)
    code = res.currency_code
    roles = ", ".join(
        f"{r.role} ({code} {r.total_cost:,.0f})" for r in res.role_breakdown
    )
    return (
        f"Estimation Results:\n"
        f"- Summary: {res.summary}\n"
        f"- Duration: {res.estimated_weeks} weeks\n"
        f"- Total Cost: {code} {res.total_cost_usd:,.2f}\n"
        f"- Role Breakdown: {roles}"
    )


def _wrap_generate_proposal_pdf(
    names: tuple[str | None, str | None] = (None, None),
    session_id: str | None = None,
) -> Callable[[str], str]:
    def run(raw_input: str) -> str:
        t = time.perf_counter()
        error: str | None = None
        try:
            res = generate_proposal_pdf(raw_input, names, session_id)
        except Exception as ex:
            error = type(ex).__name__
            raise
        finally:
            # Telemetry for the render itself, recorded here on the request
            # thread rather than inside pdf_gen — the same rule that keeps
            # docparse's usage accounting where /metrics can see it. No token
            # fields: printing a PDF spends no tokens, and reporting zeros would
            # dilute the per-model averages beside it.
            llmops.record(
                "/proposals/generate",
                (time.perf_counter() - t) * 1000,
                error=error,
                session_id=session_id,
                model=None,
            )
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
    "list, e.g. 'a3f9c2', which returns the opening of the document. When the "
    "visitor asks about a specific clause, section number, term, or topic, "
    "search for it instead by passing a query: "
    '{"upload_id": "a3f9c2", "query": "2.10"} — documents are far longer than '
    "one excerpt, so a section you cannot see in the opening is almost always "
    "present further in. Search before saying something is not in the document."
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
    session_id: str | None = None,
) -> list[ToolSpec]:
    """Build the tool list for one request.

    ``briefs`` are the uploads resolved for this session; ``names`` is the
    ``(client_name, project_name)`` the visitor supplied, if any; ``session_id``
    scopes the generated proposal on disk and in its download URL. All three are
    request-scoped, which is why this is a function and not a module constant.

    The session id is bound here rather than passed as a tool argument for the
    same reason upload ids are resolved at the API boundary: the loop dispatches
    ``Callable[[str], str]``, so anything the model can type is free text, and a
    session id it could type is a session id it could type *someone else's*.
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
            fn=_wrap_generate_proposal_pdf(names, session_id),
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
