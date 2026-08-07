"""ReAct reasoning loop for proposal generation and Q&A over NVIDIA NIM or local endpoints.

Why lightweight custom ReAct loop over LangChain AgentExecutor or LangGraph:
1. Zero heavy framework dependencies: Keeps orchestrator lightweight, provider-agnostic,
   and simple to integrate across API and Streamlit UI layers.
2. Controlled tool routing & parsing: Explicit text-driven loop avoids model-dependent function-calling
   inconsistencies (specifically on Llama 3.1 8B Instruct).
3. Built-in resiliency & retries: Direct control over tool error handling, single-retry logic,
   and observability hooks (`AgentTracer`).

This module holds the parser (pure, no I/O) and the loop runner.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from stratpoint_rag import llmops
from stratpoint_rag.agent.models import AgentResult, Link, Step
from stratpoint_rag.agent.tools import (
    TOOL_REGISTRY,
    TOOL_SPECS,
    begin_capture,
    captured_proposal_data,
    end_capture,
)
from stratpoint_rag.agent.tracer import AgentTracer, get_default_tracer
from stratpoint_rag.rag import config

log = logging.getLogger(__name__)

# The model's own line labels. Anchored per-line (re.M) because a well-formed
# turn puts each on its own line.
_THOUGHT_LINE = re.compile(r"^[ \t]*Thought:[ \t]*(.+?)[ \t]*$", re.M)
_ACTION_HEAD = re.compile(r"^[ \t]*Action:[ \t]*([A-Za-z_]\w*)[ \t]*\(", re.M)
_ANSWER_HEAD = re.compile(r"^[ \t]*Answer:[ \t]*", re.M)


@dataclass(frozen=True)
class ParsedStep:
    thoughts: list[str] = field(default_factory=list)
    tool: str | None = None
    tool_input: str | None = None
    answer: str | None = None

    @property
    def kind(self) -> str:
        if self.tool is not None:
            return "action"
        if self.answer is not None:
            return "answer"
        return "malformed"


def _unquote(raw: str) -> str:
    """Strip one matching quote pair. Inner text is preserved byte-for-byte."""
    s = raw.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def _action_arg(text: str, head_start: int) -> str | None:
    """Extract the argument of the Action on the line starting at head_start."""
    line_start = text.rfind("\n", 0, head_start) + 1
    line_end = text.find("\n", head_start)
    if line_end == -1:
        line_end = len(text)
    line = text[line_start:line_end]

    open_i = line.find("(")
    close_i = line.rfind(")")
    if open_i == -1 or close_i <= open_i:
        return None
    return _unquote(line[open_i + 1 : close_i])


def parse_step(text: str) -> ParsedStep:
    """Parse one assistant turn into thoughts plus either an action or an answer."""
    text = text or ""
    thoughts = [m.group(1) for m in _THOUGHT_LINE.finditer(text)]

    action_m = _ACTION_HEAD.search(text)
    answer_m = _ANSWER_HEAD.search(text)

    action_first = action_m is not None and (
        answer_m is None or action_m.start() < answer_m.start()
    )

    if action_first:
        arg = _action_arg(text, action_m.start())
        if arg is not None:
            return ParsedStep(thoughts=thoughts, tool=action_m.group(1), tool_input=arg)
        return ParsedStep(thoughts=thoughts)

    if answer_m is not None:
        answer = text[answer_m.end() :].strip()
        if answer:
            return ParsedStep(thoughts=thoughts, answer=answer)

    return ParsedStep(thoughts=thoughts)


# At most this many model calls per run.
MAX_TURNS = 6

STOP = ["Observation:", "PAUSE"]

_REPROMPT = "Output only a valid Action or Answer line, in the required format."

_TIMEOUT = 120

_LINK_LINE = re.compile(r"^- (.+?) \((https?://[^)]+)\)\s*$", re.MULTILINE)


def _parse_link_lines(text: str) -> list[Link]:
    """Parse '- title (url)' lines into Links."""
    return [Link(title=t.strip(), url=u.strip()) for t, u in _LINK_LINE.findall(text or "")]


def render_system_prompt(uploaded_file: str | None = None) -> str:
    """Build the loop's system prompt from TOOL_SPECS plus rules."""
    tool_lines = "\n".join(f"- {s.name}: {s.description}" for s in TOOL_SPECS)
    file_ctx = (
        f"\nAn uploaded client brief is available at: '{uploaded_file}'. "
        f"You should start by calling parse_client_brief('{uploaded_file}').\n"
        if uploaded_file
        else "\nNo file uploaded. If the visitor typed their project requirements directly in text, skip brief parsing and proceed directly to estimate_cost_and_timeline using their features.\n"
    )

    return (
        "You are Stratpoint's business-development AI assistant. You help client "
        "prospects and team members create scoped project proposals (timeline, cost, "
        "and downloadable proposal PDF).\n\n"
        f"{file_ctx}"
        "You run in a loop of Thought, Action, Observation.\n\n"
        "Tools:\n"
        f"{tool_lines}\n\n"
        "Proposal Chaining Sequence when building a proposal:\n"
        "1. parse_client_brief(file_path)\n"
        "2. estimate_cost_and_timeline(scope_input)\n"
        "3. generate_proposal_pdf(proposal_details)\n"
        "4. Answer: Summarize the proposal findings (cost, timeline, PDF link).\n\n"
        "Respond in exactly this format:\n"
        "Thought: what you need next and why\n"
        "Action: <tool name>(<input>)\n"
        "PAUSE\n\n"
        "Stop after PAUSE. The system runs the tool and replies with a line "
        "starting 'Observation:'. Never write an Observation yourself.\n\n"
        "Once you can answer, respond with:\n"
        "Answer: your reply to the visitor\n\n"
        "Rules:\n"
        "- Never mention a tool, a function name, its arguments, or any JSON. "
        "Never say what you 'called' or what something 'returns'. Just answer "
        "the visitor.\n"
        "- Summarize cost, timeline in weeks, and download links in your final Answer.\n"
    )


def _default_chat(messages: list[dict], stop: list[str]) -> str:
    key = config.nvidia_api_key()
    if not key:
        raise RuntimeError("NVIDIA_API_KEY is not set (see .envexample)")
    resp = httpx.post(
        f"{config.nvidia_base_url()}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": config.llm_model(),
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 2048,
            "stop": stop,
            "stream": False,
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    llmops.add_usage(data.get("usage"))
    return data["choices"][0]["message"].get("content") or ""


def _arg_name(tool: str) -> str:
    for s in TOOL_SPECS:
        if s.name == tool:
            return s.arg
    return "input"


def _finish(
    answer: str,
    citations: list[Link],
    resources: list[Link],
    trace: list[Step],
    thoughts: list[str],
    enable_reasoning: bool,
    tracer: AgentTracer,
) -> AgentResult:
    prop_data = captured_proposal_data()
    res = AgentResult(
        answer=answer,
        citations=citations,
        resources=resources,
        trace=trace,
        reasoning="\n\n".join(thoughts) if (enable_reasoning and thoughts) else None,
        proposal_data=prop_data,
    )
    tracer.on_agent_end(res)
    return res


def _fallback(
    message: str,
    reason: str,
    citations: list[Link],
    resources: list[Link],
    trace: list[Step],
    thoughts: list[str],
    enable_reasoning: bool,
    tracer: AgentTracer,
) -> AgentResult:
    """Answer via search_stratpoint directly when the loop can't finish."""
    trace.append(Step(type="fallback", content=reason))
    text = TOOL_REGISTRY["search_stratpoint"](message)
    citations.extend(_parse_link_lines(text))
    trace.append(Step(type="observation", tool="search_stratpoint", content=text))
    trace.append(Step(type="answer", content=text))
    return _finish(text, citations, resources, trace, thoughts, enable_reasoning, tracer)


def _execute_tool_with_retry(
    tool_name: str,
    tool_fn: Any,
    tool_input: str,
    tracer: AgentTracer,
) -> str:
    """Execute tool with 1 retry on exception and telemetry hooks."""
    tracer.on_tool_start(tool_name, tool_input)

    # First attempt
    try:
        obs = tool_fn(tool_input)
        tracer.on_tool_end(tool_name, obs)
        return str(obs)
    except Exception as ex1:
        log.warning("Tool '%s' failed on 1st attempt: %s. Retrying once...", tool_name, ex1)

    # Retry attempt
    try:
        obs = tool_fn(tool_input)
        tracer.on_tool_end(tool_name, obs)
        return str(obs)
    except Exception as ex2:
        log.error("Tool '%s' failed on retry: %s", tool_name, ex2)
        tracer.on_error(tool_name, ex2)
        return f"Error executing tool '{tool_name}': {ex2}. Please adjust arguments or proceed."


def run_react(
    message: str,
    uploaded_file: str | None = None,
    history: list[dict] | None = None,
    *,
    chat=None,
    tracer: AgentTracer | None = None,
    enable_reasoning: bool = False,
) -> AgentResult:
    """Run one turn of the plain-text ReAct loop.

    Args:
        message: The user prompt or request.
        uploaded_file: Optional path to uploaded client brief PDF or image.
        history: Prior conversation turns.
        chat: Callable seam for LLM completion (messages, stop) -> str.
        tracer: Pluggable telemetry tracer.
        enable_reasoning: Surface thoughts in AgentResult.reasoning.

    Returns:
        Structured AgentResult containing answer, trace, proposal_data, and links.
    """
    chat = chat or _default_chat
    tracer = tracer or get_default_tracer()
    tracer.on_agent_start(message, uploaded_file)

    begin_capture()
    try:
        messages: list[dict] = [{"role": "system", "content": render_system_prompt(uploaded_file)}]
        messages += [{"role": h["role"], "content": h["content"]} for h in (history or [])]
        messages.append({"role": "user", "content": message})

        trace: list[Step] = []
        citations: list[Link] = []
        resources: list[Link] = []
        thoughts: list[str] = []
        reprompted = False

        for _ in range(MAX_TURNS):
            text = chat(messages, STOP)
            messages.append({"role": "assistant", "content": text})

            step = parse_step(text)
            for t in step.thoughts:
                thoughts.append(t)
                trace.append(Step(type="thought", content=t))
                tracer.on_thought(t)

            if step.kind == "answer":
                trace.append(Step(type="answer", content=step.answer))
                return _finish(
                    step.answer, citations, resources, trace, thoughts, enable_reasoning, tracer
                )

            if step.kind == "action":
                trace.append(
                    Step(
                        type="action",
                        tool=step.tool,
                        tool_input={_arg_name(step.tool): step.tool_input},
                    )
                )
                fn = TOOL_REGISTRY.get(step.tool)
                if fn is None:
                    observation = (
                        f"Error: tool '{step.tool}' does not exist. "
                        f"Available: {', '.join(TOOL_REGISTRY)}."
                    )
                    tracer.on_error(step.tool or "unknown", observation)
                else:
                    observation = _execute_tool_with_retry(
                        step.tool, fn, step.tool_input or "", tracer
                    )
                    if step.tool == "search_stratpoint":
                        citations.extend(_parse_link_lines(observation))
                    elif step.tool == "find_resource":
                        resources.extend(_parse_link_lines(observation))

                trace.append(Step(type="observation", tool=step.tool, content=observation))
                messages.append({"role": "user", "content": f"Observation: {observation}"})
                continue

            # Malformed turn
            if reprompted:
                return _fallback(
                    message,
                    "unparseable model output after reprompt",
                    citations,
                    resources,
                    trace,
                    thoughts,
                    enable_reasoning,
                    tracer,
                )
            reprompted = True
            messages.append({"role": "user", "content": _REPROMPT})

        return _fallback(
            message,
            f"no Answer within {MAX_TURNS} turns",
            citations,
            resources,
            trace,
            thoughts,
            enable_reasoning,
            tracer,
        )
    finally:
        end_capture()
