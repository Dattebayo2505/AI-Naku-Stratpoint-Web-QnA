"""Agent tools: corpus-grounded Q&A and downloadable-resource lookup.

Both are LangChain @tool functions; the docstrings drive tool selection, so
keep them descriptive. These are the only tools the ReAct agent may call.
"""
from __future__ import annotations

import contextvars
import re

from langchain_core.tools import tool

from stratpoint_rag.rag.answer import answer_grounded as _rag_answer_grounded
from stratpoint_rag.rag.retrieve import retrieve as _retrieve

# ── Per-invocation capture ────────────────────────────────────────────────
# The ReAct agent swallows the chunks its tools retrieve, so the output
# guardrails downstream have nothing to verify the agent's answer against and
# (pre-fix) blocked every resource query as "No source chunks to verify
# against". These context-scoped sinks let the guardrail layer read back what
# the tools grounded on. begin_capture() is called by the agent path before the
# run; when it hasn't been called the sinks are None and recording is a no-op,
# so direct tool calls (and existing tests) are unaffected.
_chunk_sink: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "agent_chunk_sink", default=None
)
_grounded_sink: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "agent_grounded_sink", default=None
)


def begin_capture() -> None:
    """Start capturing tool-retrieved chunks + grounded metadata for this context."""
    _chunk_sink.set([])
    _grounded_sink.set([])


def end_capture() -> None:
    """Stop capturing and reset the sinks so later direct tool calls don't record
    into a stale bucket. Pair with begin_capture() (typically in a finally)."""
    _chunk_sink.set(None)
    _grounded_sink.set(None)


def captured_chunks() -> list:
    """Chunks the tools retrieved since the last begin_capture()."""
    return _chunk_sink.get() or []


def captured_grounded() -> list:
    """GroundedAnswer objects the search tool produced since begin_capture()."""
    return _grounded_sink.get() or []


def _record_chunks(chunks) -> None:
    sink = _chunk_sink.get()
    if sink is not None and chunks:
        sink.extend(chunks)


def _record_grounded(grounded) -> None:
    sink = _grounded_sink.get()
    if sink is not None and grounded is not None:
        sink.append(grounded)

# Markdown links whose target is a downloadable document.
_DOC_LINK = re.compile(
    r"\[([^\]]+)\]\((https?://[^\s)]+?\.(?:pdf|docx?|pptx?)(?:\?[^\s)]*)?)\)",
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


@tool
def search_stratpoint(query: str) -> str:
    """Answer a question about Stratpoint using the company's website content.
    Use for anything about Stratpoint's services, company, case studies, or blog.

    Args:
        query: The visitor's question, e.g. 'Do you offer cloud migration?'
    """
    try:
        text, chunks, grounded = _rag_answer_grounded(query)
        _record_chunks(chunks)
        _record_grounded(grounded)
        return text
    except Exception as ex:  # surfaced as an Observation so the loop can recover
        return f"search_stratpoint error: {type(ex).__name__}: {ex}"


@tool
def find_resource(topic: str) -> str:
    """Find downloadable resources (PDFs/whitepapers) related to a topic, drawn
    from Stratpoint's website content. Use when the visitor wants something to
    read or download.

    Pass the visitor's FULL request or a complete, specific phrase — keep their
    exact wording, figures, and years (e.g. 'business tasks automated by 2027').
    Do NOT shorten it to broad keywords: resources are matched against source
    text, so terse topics often miss the document that mentions them.

    Args:
        topic: The full, specific subject to find resources for, e.g.
            'how many business tasks will be automated by 2027'
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


TOOLS = [search_stratpoint, find_resource]
