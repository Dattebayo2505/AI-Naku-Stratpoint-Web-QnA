"""ReAct agent over the NVIDIA NIM cloud endpoint.

This module holds the result models + trace extraction (pure) and, added in a
later task, the agent runner. Public seam: run_agent(message, history=None).
"""
from __future__ import annotations

import re
from typing import Any

from stratpoint_rag.agent.models import AgentResult, Link, Step

_LINK_LINE = re.compile(r"^- (.+?) \((https?://[^)]+)\)\s*$", re.MULTILINE)


def _parse_link_lines(text: str) -> list[Link]:
    """Parse '- title (url)' lines into Links (used for both citations & resources)."""
    return [Link(title=t.strip(), url=u.strip()) for t, u in _LINK_LINE.findall(text or "")]


def _build_result(messages: list[Any]) -> AgentResult:
    """Fold a LangGraph message list into an AgentResult (answer/trace/citations/resources)."""
    trace: list[Step] = []
    citations: list[Link] = []
    resources: list[Link] = []
    reasonings: list[str] = []
    answer = ""

    for m in messages:
        mtype = getattr(m, "type", None)
        tool_calls = getattr(m, "tool_calls", None) or []
        content = getattr(m, "content", "") or ""

        if mtype == "ai":
            ak = getattr(m, "additional_kwargs", {}) or {}
            rc = ak.get("reasoning_content") or ak.get("reasoning")
            if rc:
                reasonings.append(rc)
                trace.append(Step(type="reasoning", content=rc))
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

    return AgentResult(
        answer=answer,
        citations=citations,
        resources=resources,
        trace=trace,
        reasoning="\n\n".join(reasonings) if reasonings else None,
    )


from stratpoint_rag.rag import config
from stratpoint_rag.agent.tools import TOOLS

# Tuned against meta/llama-3.1-8b-instruct (see docs/general-log.md). Two rules
# here are load-bearing and were added after live ablation:
#   1. The tool-routing block. The previous prompt only described find_resource,
#      so an 8B model called it for plain questions and answered "not available
#      in the downloadable resources". gemma-31b guessed right and masked this.
#   2. "Never mention a tool / just answer". Without it llama's final message
#      narrates its own call ("The find_resource function is called with...")
#      instead of answering, and never restates the PDF links.
# Deliberately NO literal response template: an example block gets copied onto
# plain questions, turning "who leads Stratpoint?" into a bare link list.
SYSTEM_PROMPT = (
    "You are Stratpoint's website assistant. You are talking directly to a visitor.\n\n"
    "Choosing a tool:\n"
    "- search_stratpoint - for any question about Stratpoint: the company, its people "
    "and leaders, services, case studies, or blog posts. This is the default.\n"
    "- find_resource - ONLY when the visitor asks for a document to read or download "
    "(a PDF, report, whitepaper). Pass it the visitor's full, specific wording (keep "
    "their figures and years); do not shorten the topic to keywords.\n\n"
    "Writing the final message:\n"
    "- Never mention a tool, a function name, its arguments, or any JSON. Never say "
    "what you 'called' or what something 'returns'. Just answer the visitor.\n"
    "- Answer the visitor's actual question in your own words, in prose, using what "
    "the tool returned. State the facts they asked for.\n"
    "- When find_resource returned links, include every one of them in your reply as "
    "a markdown link with its full URL.\n"
)

# One compiled agent per reasoning flag (thinking-on vs off), built lazily.
_agents: dict[bool, object] = {}


def _build_agent(enable_reasoning: bool = False):
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
        # ChatNVIDIA's default read timeout is 60s. On llama-3.1-8b a turn is
        # ~1-5s, but NIM latency spikes hard under endpoint load (gemma turns
        # were measured at 3.5s and 73s on the same prompt hours apart), so the
        # headroom stays. Kept as a literal, not config.llm_timeout(), because
        # that default (300s) would park a worker thread on a hung cloud call.
        timeout=120,
        # LIVE-CONFIRMED: pass as a constructor kwarg (transfers to model_kwargs
        # with a harmless warning); extra_body fails silently. Enables NIM's
        # native thinking and populates additional_kwargs["reasoning_content"].
        chat_template_kwargs={"enable_thinking": enable_reasoning},
    )
    return create_agent(llm, TOOLS, system_prompt=SYSTEM_PROMPT)


def _get_agent(enable_reasoning: bool = False):
    if enable_reasoning not in _agents:
        _agents[enable_reasoning] = _build_agent(enable_reasoning)
    return _agents[enable_reasoning]


def run_agent(
    message: str,
    history: list[dict] | None = None,
    *,
    agent=None,
    enable_reasoning: bool = False,
) -> AgentResult:
    """Run one turn of the ReAct agent and return a structured AgentResult.

    `agent` is an injection seam for tests; production uses the per-flag cache.
    """
    if agent is None:
        agent = _get_agent(enable_reasoning)
    msgs: list = [(h["role"], h["content"]) for h in (history or [])]
    msgs.append(("user", message))
    state = agent.invoke({"messages": msgs}, config={"recursion_limit": 8})
    return _build_result(state["messages"])
