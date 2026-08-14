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
    TRUNCATION_MARKER,
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

# Re-running a tool with the same input cannot change the answer; it only grows
# the context. Measured: asked what an attached 9-page brief was about, the
# model emitted a byte-identical Thought/Action turn six times running, and the
# loop obligingly re-executed `read_brief` and re-appended the same 6 KB
# observation each time. The state it conditioned on never changed, so neither
# did its output — it burned every turn and fell through to the fallback. The
# feedback has to *differ* from the last one for the loop to move.
_REPEAT_OBSERVATION = (
    "You already called {tool} with that exact input, and its result is above. "
    "Do not call it again. Answer the visitor now, using what it returned, "
    "beginning your reply with 'Answer:'."
)

# ...but "answer now" is the wrong instruction when what came back was only part
# of the document. The plain nudge above says nothing about the truncation and
# nothing about flagging it, so a repeat over a partial excerpt produced a
# confident, unqualified summary of a document the loop had read a third of —
# even though the excerpt itself ends with "Say so if you summarize from this
# excerpt alone". Reported live against an 18-page deck summarized from its
# first 8 pages, with the 2 unreadable pages unmentioned too.
#
# Offering the search back is safe precisely because the repeat guard stands: a
# byte-identical retry lands here again rather than re-running the tool, so the
# only way forward is a *different* query — which is the escape hatch
# `read_brief`'s query parameter was added to provide.
_REPEAT_TRUNCATED = (
    "You already called {tool} with that exact input. What it returned, above, "
    "is only the first part of the document — not all of it. Do not repeat that "
    "call. Either search another part by calling {tool} once more with a "
    'DIFFERENT search term, as {{"upload_id": "{upload_id}", "query": "<words '
    'to find>"}}, or answer the visitor now, beginning your reply with '
    "'Answer:'. If you answer from the part you have already seen, say in your "
    "reply that you have seen only part of the document."
)


def _repeat_nudge(tool: str, prior: str, briefs: list[BriefRef] | None) -> str:
    """Feedback for a byte-identical repeated call.

    Tool-aware on purpose: telling the model to answer from what it has is right
    when it has the whole thing and wrong when it has an excerpt. The truncation
    is read off the prior observation rather than re-derived from the excerpt
    cap, so the two cannot disagree.
    """
    if tool == READ_BRIEF_TOOL_NAME and TRUNCATION_MARKER in prior:
        upload_id = briefs[0].upload_id if briefs and len(briefs) == 1 else "the id above"
        return _REPEAT_TRUNCATED.format(tool=tool, upload_id=upload_id)
    return _REPEAT_OBSERVATION.format(tool=tool)

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
            'estimate_cost_and_timeline({"features": ["Feature 1", "Feature 2", "Feature 3", "Feature 4"], "timeline_weeks": 6.0})',
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
        "- Execution phases and roadmaps are dynamic (not restricted to 3 phases). You can pass custom_phases or timeline_weeks to estimate_cost_and_timeline to tailor the roadmap.\n"
        "- If target launch date or timeline info is missing from the brief and user query, ask the visitor if they have a target launch date or project duration in mind to gauge the timeline accurately.\n"
        "- Summarize cost, timeline in weeks, and download links in your final Answer.\n"
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
    """One completion. **An empty ``stop`` is omitted, never sent as ``[]``.**

    NIM rejects an empty stop array outright::

        400  Validation: Stop sequences array cannot be empty

    and the one caller that passes no stop sequences is `_brief_fallback` — the
    safety net that answers from the visitor's own document when the ReAct loop
    stalls. So the fallback raised on every single invocation against this
    endpoint and degraded to its "I wasn't able to summarize it just now"
    string, which is indistinguishable from the model having nothing to say.
    Measured live: a question answerable only past the excerpt cap stalled the
    loop, reached the fallback, and returned that apology with the answer
    sitting in the trace.

    The whole test suite injects a fake ``chat``, so nothing offline could see
    it — the payload is asserted directly in `test_react_loop.py` instead.
    """
    key = config.nvidia_api_key()
    if not key:
        raise RuntimeError("NVIDIA_API_KEY is not set (see .envexample)")
    payload: dict[str, Any] = {
        "model": config.llm_model(),
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 2048,
        "stream": False,
    }
    if stop:
        payload["stop"] = stop
    resp = httpx.post(
        f"{config.nvidia_base_url()}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json=payload,
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


_BRIEF_FALLBACK_SYSTEM = (
    "Answer the visitor's question using only the document excerpt below. If it "
    "does not contain the answer, say so plainly. Never mention tools, "
    "excerpts, or how you got this text.\n\n{excerpt}"
)

_BRIEF_FALLBACK_FAILED = (
    "I have your document, but I wasn't able to summarize it just now. Could "
    "you tell me which part of it you'd like me to look at?"
)


def _first_brief_observation(trace: list[Step]) -> str | None:
    """The document text the loop already pulled this run, if any.

    Scanned forwards, not backwards: the *last* brief observation on a stalled
    turn is the repeat nudge, which is traced under the same tool name and
    carries no document text at all. Only a genuine execution can precede a
    repeat, so the first one is always the real thing.
    """
    for step in trace:
        if step.type == "observation" and step.tool in (
            READ_BRIEF_TOOL_NAME,
            BRIEF_TOOL_NAME,
        ):
            return step.content
    return None


def _brief_fallback(
    message: str,
    briefs: list[BriefRef],
    trace: list[Step],
    chat: Any,
    registry: dict[str, Any],
) -> str:
    """Answer from the attached document when the loop couldn't finish.

    **The website corpus is not the fallback when a document is attached.** The
    old code called ``search_stratpoint`` unconditionally, so a loop that stalled
    six turns deep in the visitor's own 9-page RFP answered from stratpoint.com
    instead — about digital advertising in general, with two portfolio pages
    cited underneath. Confidently wrong and dressed as verified: the citations
    are real, they are just sources for a question nobody asked.

    One plain completion, no ReAct framing: the loop has already demonstrated it
    cannot hold the format, and all that is left to do is summarize text we hold.
    """
    excerpt = _first_brief_observation(trace)
    if excerpt is None:
        read = registry.get(READ_BRIEF_TOOL_NAME)
        if read is None:
            return _BRIEF_FALLBACK_FAILED
        try:
            excerpt = str(read(briefs[0].upload_id))
        except Exception as ex:
            log.warning("brief fallback could not read the document: %s", ex)
            return _BRIEF_FALLBACK_FAILED

    try:
        text = chat(
            [
                {
                    "role": "system",
                    "content": _BRIEF_FALLBACK_SYSTEM.format(excerpt=excerpt),
                },
                {"role": "user", "content": message},
            ],
            [],
        )
    except Exception as ex:
        log.warning("brief fallback summarization failed: %s", ex)
        return _BRIEF_FALLBACK_FAILED

    return text.strip() or _BRIEF_FALLBACK_FAILED


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
    briefs: list[BriefRef] | None = None,
    chat: Any = None,
) -> AgentResult:
    """Answer without the loop when it can't finish.

    Which corpus is *not* a detail: with a document attached the answer comes
    from that document, and only otherwise from the website.
    """
    trace.append(Step(type="fallback", content=reason))

    if briefs:
        text = _brief_fallback(message, briefs, trace, chat, registry)
        trace.append(Step(type="answer", content=text))
        return _finish(
            text, citations, resources, trace, thoughts, enable_reasoning, tracer
        )

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
    session_id: str | None = None,
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
        session_id: Scopes a generated proposal PDF on disk and in its download
            URL. Bound into the tool, never exposed as a tool argument.
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
    specs = build_tool_specs(briefs, names, session_id)
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
        # (tool, input) already run this turn -> what it returned. The value is
        # kept, not just the key, because `_repeat_nudge` decides what to say
        # from whether that result was a truncated excerpt.
        executed: dict[tuple[str, str], str] = {}

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
                elif (step.tool, step.tool_input or "") in executed:
                    observation = _repeat_nudge(
                        step.tool, executed[(step.tool, step.tool_input or "")], briefs
                    )
                else:
                    observation = _execute_tool_with_retry(
                        step.tool, fn, step.tool_input or "", tracer
                    )
                    executed[(step.tool, step.tool_input or "")] = observation
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
                    briefs,
                    chat,
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
            briefs,
            chat,
        )
    finally:
        end_capture()
