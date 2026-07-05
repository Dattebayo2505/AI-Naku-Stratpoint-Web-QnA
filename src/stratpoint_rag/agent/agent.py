"""ReAct agent over the NVIDIA NIM cloud endpoint.

This module holds the result models + trace extraction (pure) and, added in a
later task, the agent runner. Public seam: run_agent(message, history=None).
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel


class Link(BaseModel):
    title: str
    url: str


class Step(BaseModel):
    type: str  # "thought" | "action" | "observation" | "answer"
    tool: str | None = None
    tool_input: dict | None = None
    content: str | None = None


class AgentResult(BaseModel):
    answer: str
    citations: list[Link] = []
    resources: list[Link] = []
    trace: list[Step] = []


_LINK_LINE = re.compile(r"^- (.+?) \((https?://[^)]+)\)\s*$", re.MULTILINE)


def _parse_link_lines(text: str) -> list[Link]:
    """Parse '- title (url)' lines into Links (used for both citations & resources)."""
    return [Link(title=t.strip(), url=u.strip()) for t, u in _LINK_LINE.findall(text or "")]


def _build_result(messages: list[Any]) -> AgentResult:
    """Fold a LangGraph message list into an AgentResult (answer/trace/citations/resources)."""
    trace: list[Step] = []
    citations: list[Link] = []
    resources: list[Link] = []
    answer = ""

    for m in messages:
        mtype = getattr(m, "type", None)
        tool_calls = getattr(m, "tool_calls", None) or []
        content = getattr(m, "content", "") or ""

        if mtype == "ai":
            if content and tool_calls:
                trace.append(Step(type="thought", content=content))
            elif content:
                answer = content
                trace.append(Step(type="answer", content=content))
            for tc in tool_calls:
                trace.append(Step(type="action", tool=tc["name"], tool_input=tc.get("args") or {}))
        elif mtype == "tool":
            name = getattr(m, "name", None)
            trace.append(Step(type="observation", tool=name, content=content))
            if name == "search_stratpoint":
                citations.extend(_parse_link_lines(content))
            elif name == "find_resource":
                resources.extend(_parse_link_lines(content))

    return AgentResult(answer=answer, citations=citations, resources=resources, trace=trace)


from stratpoint_rag.rag import config
from stratpoint_rag.agent.tools import TOOLS

SYSTEM_PROMPT = (
    "Use the find_resource tool when the visitor "
    "wants something to read or download — and pass it the visitor's full, specific "
    "wording (keep their figures and years); do not shorten the topic to keywords.\n"
)

_agent = None


def _build_agent():
    key = config.nvidia_api_key()
    if not key:
        raise RuntimeError("NVIDIA_API_KEY is not set (see .envexample)")
    from langchain_nvidia_ai_endpoints import ChatNVIDIA
    from langchain.agents import create_agent

    llm = ChatNVIDIA(
        base_url=config.nvidia_base_url(),
        model=config.llm_model(),
        api_key=key,
        temperature=0.2,
        # ChatNVIDIA's default read timeout is 60s; the agent's steady-state
        # latency is ~40s (several NIM round-trips per turn), so a transient
        # spike would 502. Match rag.answer's 120s for comfortable headroom.
        timeout=120,
    )
    return create_agent(llm, TOOLS, system_prompt=SYSTEM_PROMPT)


def _get_agent():
    global _agent
    if _agent is None:
        _agent = _build_agent()
    return _agent


def run_agent(message: str, history: list[dict] | None = None, *, agent=None) -> AgentResult:
    """Run one turn of the ReAct agent and return a structured AgentResult.

    `agent` is an injection seam for tests; production uses the lazy singleton.
    """
    if agent is None:
        agent = _get_agent()
    msgs: list = [(h["role"], h["content"]) for h in (history or [])]
    msgs.append(("user", message))
    state = agent.invoke({"messages": msgs}, config={"recursion_limit": 8})
    return _build_result(state["messages"])
