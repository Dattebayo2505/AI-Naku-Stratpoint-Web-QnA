"""Agent tools: corpus-grounded Q&A and downloadable-resource lookup.

Both are LangChain @tool functions; the docstrings drive tool selection, so
keep them descriptive. These are the only tools the ReAct agent may call.
"""
from __future__ import annotations

import re

from langchain_core.tools import tool

from stratpoint_rag.rag.answer import answer as _rag_answer
from stratpoint_rag.rag.retrieve import retrieve as _retrieve

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
        text, _ = _rag_answer(query)
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
