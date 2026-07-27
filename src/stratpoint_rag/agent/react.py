"""Plain-text ReAct loop over the NVIDIA NIM cloud endpoint.

Why plain text rather than native function-calling: on
meta/llama-3.1-8b-instruct the native path misroutes between the two tools and
narrates its own calls into the final message. Driving the loop in text lets us
own the routing prompt, the parsing, and the recovery — and drops the LangChain
dependency stack, since nothing here is provider-specific except the URL.

This module holds the parser (pure, no I/O) and, added in a later task, the
loop itself.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

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
