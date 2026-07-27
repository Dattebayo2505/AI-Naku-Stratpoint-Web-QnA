"""Agent tools: corpus-grounded Q&A and downloadable-resource lookup.

Both are plain callables. Tool selection is driven by the `description` on
their ToolSpec below, which is rendered into the ReAct loop's system prompt.
These are the only tools the agent may call.
"""
from __future__ import annotations

import contextvars
import re
from dataclasses import dataclass
from typing import Callable

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

# Markdown links whose target is a downloadable document. PDF only: the crawled
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


@dataclass(frozen=True)
class ToolSpec:
    """One tool the ReAct loop may call.

    `description` is rendered into the loop's system prompt, so a tool cannot be
    added without the prompt learning about it — which is how the old setup
    drifted (descriptions lived in both the docstring and SYSTEM_PROMPT).
    `arg` is the parameter name, used only to label the trace step.
    """

    name: str
    fn: Callable[[str], str]
    arg: str
    description: str


# Wording here is load-bearing and was tuned against live llama-3.1-8b:
#   - search_stratpoint must be named the DEFAULT, or an 8B model calls
#     find_resource for plain questions and answers "not available in the
#     downloadable resources".
#   - find_resource must demand the visitor's FULL wording; resources are
#     matched against source text, so terse topics miss the document.
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
]

TOOL_REGISTRY: dict[str, Callable[[str], str]] = {s.name: s.fn for s in TOOL_SPECS}
