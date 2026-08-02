"""Plain-text ReAct loop over the NVIDIA NIM cloud endpoint.

Why plain text rather than native function-calling: on
meta/llama-3.1-8b-instruct the native path misroutes between the two tools and
narrates its own calls into the final message. Driving the loop in text lets us
own the routing prompt, the parsing, and the recovery — and drops the LangChain
dependency stack, since nothing here is provider-specific except the URL.

This module holds the parser (pure, no I/O) and the loop itself.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx

from stratpoint_rag import llmops
from stratpoint_rag.agent.models import AgentResult, Link, Step
from stratpoint_rag.agent.tools import TOOL_REGISTRY, TOOL_SPECS
from stratpoint_rag.rag import config

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
    """Strip one matching quote pair. Inner text is preserved byte-for-byte —
    find_resource's accuracy depends on the visitor's exact figures and years."""
    s = raw.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def _action_arg(text: str, head_start: int) -> str | None:
    """Extract the argument of the Action on the line starting at head_start.

    Takes everything between the FIRST '(' and the LAST ')' on that one line.
    Greedy is deliberate: a non-greedy match truncates arguments containing
    parentheses. Restricting to a single line keeps that greed bounded — both
    tool arguments are short strings, never multi-line.
    """
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
    """Parse one assistant turn into thoughts plus either an action or an answer.

    When both an Action and an Answer are present, whichever starts EARLIER in
    the text wins. An 8B model routinely appends a speculative answer after an
    action it has not run; honoring the action keeps the loop grounded.
    """
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
        # Unbalanced parentheses — fall through to malformed so the loop
        # reprompts rather than dispatching a tool with a guessed argument.
        return ParsedStep(thoughts=thoughts)

    if answer_m is not None:
        answer = text[answer_m.end() :].strip()
        if answer:
            return ParsedStep(thoughts=thoughts, answer=answer)

    return ParsedStep(thoughts=thoughts)


# At most this many model calls per run. Covers the deepest legitimate path
# (search -> find_resource -> answer) with one spare, and bounds latency
# against the NVIDIA free tier's rate limiting. A spent reprompt uses one.
MAX_TURNS = 4

# Cuts generation before the model can write its own Observation and answer
# from an invented tool result. Because `stop` CONSUMES the token, nothing
# downstream may require a literal PAUSE to be present.
STOP = ["Observation:", "PAUSE"]

_REPROMPT = "Output only a valid Action or Answer line, in the required format."

# NIM latency spikes hard under endpoint load (gemma turns were measured at
# 3.5s and 73s on the same prompt hours apart), so the headroom stays. Kept as
# a literal rather than config.llm_timeout(), whose 300s default would park a
# worker thread on a hung cloud call.
_TIMEOUT = 120

_LINK_LINE = re.compile(r"^- (.+?) \((https?://[^)]+)\)\s*$", re.MULTILINE)


def _parse_link_lines(text: str) -> list[Link]:
    """Parse '- title (url)' lines into Links (used for citations & resources)."""
    return [Link(title=t.strip(), url=u.strip()) for t, u in _LINK_LINE.findall(text or "")]


def render_system_prompt() -> str:
    """Build the loop's system prompt from TOOL_SPECS plus the fixed rules.

    Deliberately NO literal example answer: an example block gets copied onto
    plain questions, turning "who leads Stratpoint?" into a bare link list.
    """
    tool_lines = "\n".join(f"- {s.name}: {s.description}" for s in TOOL_SPECS)
    return (
        "You are Stratpoint's website assistant. You are talking directly to a "
        "visitor.\n\n"
        "You run in a loop of Thought, Action, Observation.\n\n"
        "Tools:\n"
        f"{tool_lines}\n\n"
        "Respond in exactly this format:\n"
        "Thought: what you need next and why\n"
        "Action: <tool name>(<input>)\n"
        "PAUSE\n\n"
        "Replace <tool name> with one of the tool names listed above and "
        "<input> with the actual text to search for. LIVE-CONFIRMED failure: "
        "llama copies this template literally and calls "
        "find_resource(input string), wasting every turn. Never write the "
        "words 'tool name', 'input', or the angle brackets.\n\n"
        "Stop after PAUSE. The system runs the tool and replies with a line "
        "starting 'Observation:'. Never write an Observation yourself.\n\n"
        "Once you can answer, respond with:\n"
        "Answer: your reply to the visitor\n\n"
        "When to stop searching:\n"
        "- Never repeat an Action you have already run with the same input.\n"
        "- If an Observation says nothing was found, do NOT retry the same "
        "tool with a reworded input more than once. Move on: either try "
        "search_stratpoint, or write your Answer using what you already have.\n"
        "- Say plainly that you could not find a downloadable document rather "
        "than searching again. An honest Answer beats another Action.\n\n"
        "Writing the Answer:\n"
        "- Never mention a tool, a function name, its arguments, or any JSON. "
        "Never say what you 'called' or what something 'returns'. Just answer "
        "the visitor.\n"
        "- Answer the visitor's actual question in your own words, in prose, "
        "using what the Observations returned. State the facts they asked for.\n"
        "- When find_resource returned links, include every one of them in your "
        "reply as a markdown link with its full URL.\n"
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
    llmops.add_usage(data.get("usage"))  # per-request token accumulator
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
) -> AgentResult:
    return AgentResult(
        answer=answer,
        citations=citations,
        resources=resources,
        trace=trace,
        reasoning="\n\n".join(thoughts) if (enable_reasoning and thoughts) else None,
    )


def _fallback(
    message: str,
    reason: str,
    citations: list[Link],
    resources: list[Link],
    trace: list[Step],
    thoughts: list[str],
    enable_reasoning: bool,
) -> AgentResult:
    """Answer via search_stratpoint directly when the loop can't finish.

    Calling the tool (rather than returning a canned message) matters: it
    records chunks into the capture sink, so the output guardrails still have
    sources to verify against. A sourceless answer gets blocked downstream.
    """
    trace.append(Step(type="fallback", content=reason))
    text = TOOL_REGISTRY["search_stratpoint"](message)
    citations.extend(_parse_link_lines(text))
    trace.append(Step(type="observation", tool="search_stratpoint", content=text))
    trace.append(Step(type="answer", content=text))
    return _finish(text, citations, resources, trace, thoughts, enable_reasoning)


def run_react(
    message: str,
    history: list[dict] | None = None,
    *,
    chat=None,
    enable_reasoning: bool = False,
) -> AgentResult:
    """Run one turn of the plain-text ReAct loop.

    `chat` is the injection seam for tests: a callable (messages, stop) -> str.
    Production passes None and gets the httpx-backed NIM client.
    """
    chat = chat or _default_chat

    messages: list[dict] = [{"role": "system", "content": render_system_prompt()}]
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

        if step.kind == "answer":
            trace.append(Step(type="answer", content=step.answer))
            return _finish(step.answer, citations, resources, trace, thoughts, enable_reasoning)

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
                # Recoverable, and deliberately does NOT spend the reprompt:
                # naming the real tools is usually enough to get back on track.
                observation = (
                    f"Error: tool '{step.tool}' does not exist. "
                    f"Available: {', '.join(TOOL_REGISTRY)}."
                )
            else:
                observation = fn(step.tool_input)
                if step.tool == "search_stratpoint":
                    citations.extend(_parse_link_lines(observation))
                elif step.tool == "find_resource":
                    resources.extend(_parse_link_lines(observation))
            trace.append(Step(type="observation", tool=step.tool, content=observation))
            messages.append({"role": "user", "content": f"Observation: {observation}"})
            continue

        # Malformed. One corrective reprompt per run, not per turn — it costs a
        # turn, and the free tier rate-limits hard.
        if reprompted:
            return _fallback(
                message,
                "unparseable model output after reprompt",
                citations, resources, trace, thoughts, enable_reasoning,
            )
        reprompted = True
        messages.append({"role": "user", "content": _REPROMPT})

    return _fallback(
        message,
        f"no Answer within {MAX_TURNS} turns",
        citations, resources, trace, thoughts, enable_reasoning,
    )
