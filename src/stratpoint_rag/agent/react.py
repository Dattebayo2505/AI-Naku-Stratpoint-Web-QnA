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
    BRIEF_TOOL_NAME,
    READ_BRIEF_TOOL_NAME,
    ToolSpec,
    begin_capture,
    build_tool_registry,
    build_tool_specs,
    captured_proposal_data,
    end_capture,
)
from stratpoint_rag.docparse import BriefRef
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


def render_attachment_manifest(briefs: list[BriefRef] | None) -> str:
    """List the visitor's uploads, with their ids, for the system prompt.

    **This is non-negotiable when an attachment exists.** The tool takes an
    opaque `upload_id`, and without the manifest that id never reaches the model
    — the tool is uncallable by construction. Worse, the failure is silent and
    confident: asked "what's the timeline on this?" with a brief attached, a
    model that does not know a document exists calls `search_stratpoint`, whose
    description says it is the default tool, and answers from the website corpus
    about entirely the wrong thing.

    Example line::

        - client-brief.pdf | id=a3f9c2 | 12 pages, transcribed
    """
    if not briefs:
        return ""

    lines = []
    for brief in briefs:
        pages = f"{brief.pages_total} page" + ("" if brief.pages_total == 1 else "s")
        state = "transcribed" if brief.transcribed else "not transcribed yet"
        if brief.pages_failed:
            state += f", {len(brief.pages_failed)} page(s) unreadable"
        lines.append(f"- {brief.filename} | id={brief.upload_id} | {pages}, {state}")

    return (
        "\nThe visitor has attached these documents:\n"
        + "\n".join(lines)
        + "\nWhen they refer to 'the brief', 'the RFP', 'this document', or "
        "their own project, they mean the file above — use its id.\n"
        f"Call {READ_BRIEF_TOOL_NAME} to answer a question about what it says, "
        f"and {BRIEF_TOOL_NAME} to scope or price the work in it.\n"
    )


def render_system_prompt(
    briefs: list[BriefRef] | None = None,
    specs: list[ToolSpec] | None = None,
    *,
    proposal_mode: bool = True,
) -> str:
    """Build the loop's system prompt from this request's tool specs plus rules.

    ``proposal_mode`` is the difference between "quote this brief" and "what is
    this brief about". It was not a parameter, and every attachment question
    therefore ran under a prompt whose identity line was *"you help prospects
    create scoped project proposals"*, whose only worked example was the
    four-step proposal chain, and which carried the standing rule *"summarize
    cost, timeline in weeks, and download links in your final Answer"*. Asked
    for a two-sentence description of an uploaded document, the loop did as it
    was told and returned a $27k quote.

    Both modes get the same tools: narrowing the toolset instead would leave the
    loop unable to change course when the visitor's next message asks for the
    proposal after all.
    """
    specs = specs if specs is not None else build_tool_specs(briefs)
    tool_lines = "\n".join(f"- {s.name}: {s.description}" for s in specs)

    if briefs:
        file_ctx = render_attachment_manifest(briefs)
    else:
        file_ctx = (
            "\nNo document is attached. If the visitor typed their project "
            "requirements directly in text, work from those. Do not ask them to "
            "upload anything unless they bring it up.\n"
        )

    header = (
        "You are Stratpoint's business-development AI assistant. You help client "
        "prospects and team members create scoped project proposals (timeline, "
        "cost, and downloadable proposal PDF).\n\n"
        if proposal_mode
        else "You are Stratpoint's AI assistant. You answer visitors' questions "
        "about Stratpoint and about any document they have uploaded.\n\n"
    )

    if proposal_mode:
        steps = [f"{BRIEF_TOOL_NAME}(upload_id)"] if briefs else []
        steps += [
            "estimate_cost_and_timeline(scope_input)",
            "generate_proposal_pdf(proposal_details)",
            "Answer: Summarize the proposal findings (cost, timeline, PDF link).",
        ]
        chain = "".join(f"{i}. {s}\n" for i, s in enumerate(steps, start=1))
        task = f"Proposal Chaining Sequence when building a proposal:\n{chain}\n"
    else:
        task = (
            "The visitor is NOT asking for a proposal. Answer the question they "
            "actually asked.\n"
            f"- To answer anything about an uploaded document — what it is, what "
            f"it says, a summary — call {READ_BRIEF_TOOL_NAME} and answer from "
            "what it returns.\n"
            "- For questions about Stratpoint, call search_stratpoint.\n\n"
        )

    closing = (
        "- Summarize cost, timeline in weeks, and download links in your final "
        "Answer.\n"
        if proposal_mode
        else "- Do NOT produce a cost, a timeline, a role breakdown, or a "
        "proposal PDF. The visitor did not ask for one; offer to build one only "
        "if it is genuinely useful, and wait for them to say yes.\n"
        "- Match the length the visitor asked for. If they asked for two "
        "sentences, give two sentences.\n"
    )

    return (
        f"{header}"
        f"{file_ctx}"
        "You run in a loop of Thought, Action, Observation.\n\n"
        "Tools:\n"
        f"{tool_lines}\n\n"
        f"{task}"
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
        "- Never invent a client name, a company name, or a project name. If the "
        "document does not state one, leave it out.\n"
        f"{closing}"
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


def _arg_name(tool: str, specs: list[ToolSpec]) -> str:
    for s in specs:
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
    registry: dict[str, Any],
) -> AgentResult:
    """Answer via search_stratpoint directly when the loop can't finish."""
    trace.append(Step(type="fallback", content=reason))
    text = registry["search_stratpoint"](message)
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
    briefs: list[BriefRef] | None = None,
    names: tuple[str | None, str | None] = (None, None),
    proposal_mode: bool = True,
) -> AgentResult:
    """Run one turn of the plain-text ReAct loop.

    Args:
        message: The user prompt or request.
        uploaded_file: Legacy display label for the tracer only. It no longer
            reaches the prompt: the loop addresses uploads by id via ``briefs``,
            never by path.
        history: Prior conversation turns.
        chat: Callable seam for LLM completion (messages, stop) -> str.
        tracer: Pluggable telemetry tracer.
        enable_reasoning: Surface thoughts in AgentResult.reasoning.
        briefs: Uploads resolved for this session. Non-empty is what registers
            the brief tool and puts the attachment manifest in the prompt.
        names: ``(client_name, project_name)`` the visitor supplied, if any.
        proposal_mode: True when the visitor asked for a proposal/quote. False
            casts the same tools as document Q&A instead, and drops the standing
            instruction to end every Answer with cost, timeline and a PDF link.

    Returns:
        Structured AgentResult containing answer, trace, proposal_data, and links.
    """
    chat = chat or _default_chat
    tracer = tracer or get_default_tracer()
    tracer.on_agent_start(message, uploaded_file)

    # Built per request: the tool list depends on what is attached, and the PDF
    # tool closes over the visitor's answer about naming.
    specs = build_tool_specs(briefs, names)
    registry = build_tool_registry(specs)

    begin_capture()
    try:
        messages: list[dict] = [
            {
                "role": "system",
                "content": render_system_prompt(
                    briefs, specs, proposal_mode=proposal_mode
                ),
            }
        ]
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
                        tool_input={_arg_name(step.tool, specs): step.tool_input},
                    )
                )
                fn = registry.get(step.tool)
                if fn is None:
                    observation = (
                        f"Error: tool '{step.tool}' does not exist. "
                        f"Available: {', '.join(registry)}."
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
                    registry,
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
            registry,
        )
    finally:
        end_capture()
