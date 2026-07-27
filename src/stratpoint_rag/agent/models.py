"""Result models for the ReAct agent path.

These live in their own module so `react.py` (which builds them) and `agent.py`
(which calls into `react.py`) don't import each other.
"""
from __future__ import annotations

from pydantic import BaseModel


class Link(BaseModel):
    title: str
    url: str


class Step(BaseModel):
    # "thought" | "action" | "observation" | "answer" | "fallback"
    type: str
    tool: str | None = None
    tool_input: dict | None = None
    content: str | None = None


class AgentResult(BaseModel):
    answer: str
    citations: list[Link] = []
    resources: list[Link] = []
    trace: list[Step] = []
    # Grounding + guardrail metadata surfaced to the UI debug panel. Optional so
    # the ReAct path (which has no grounding score of its own) and existing
    # callers/tests keep working; populated by run_with_guardrails.
    is_grounded: bool | None = None
    confidence: float | None = None
    guardrail_reason: str | None = None
    # The loop's own "Thought:" lines, joined. Surfaced only when the caller
    # passes enable_reasoning=True. This is PROMPTED reasoning, not a model
    # feature: NIM's endpoint for meta/llama-3.1-8b-instruct does not support
    # native thinking, which is why enable_thinking was removed.
    reasoning: str | None = None
