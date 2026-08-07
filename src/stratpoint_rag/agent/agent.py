"""Public seam for the ReAct agent path.

The loop itself lives in `react.py`; the result models in `models.py`. This
module stays as the import point callers use (`from stratpoint_rag.agent.agent import ...`),
and re-exports the models so those imports keep resolving.
"""
from __future__ import annotations

from stratpoint_rag.agent.models import AgentResult, Link, ProposalData, Step
from stratpoint_rag.agent.react import run_react
from stratpoint_rag.agent.tracer import AgentTracer

__all__ = ["AgentResult", "Link", "Step", "ProposalData", "run_agent"]


def run_agent(
    message: str,
    uploaded_file: str | list[dict] | None = None,
    history: list[dict] | None = None,
    *,
    chat=None,
    tracer: AgentTracer | None = None,
    enable_reasoning: bool = False,
) -> AgentResult:
    """Run one turn of the ReAct agent and return a structured AgentResult.

    Args:
        message: User prompt or query string.
        uploaded_file: Optional file path string to an uploaded client brief PDF or image.
                       Supports positional passing for backward compatibility if a list is passed.
        history: Conversation history list.
        chat: Injection seam for tests: callable (messages, stop) -> str.
        tracer: Telemetry tracer implementing AgentTracer ABC.
        enable_reasoning: Controls whether thoughts are surfaced in AgentResult.reasoning.

    Returns:
        Structured AgentResult containing answer, trace, citations, resources, and proposal_data.
    """
    if isinstance(uploaded_file, list):
        history = uploaded_file
        uploaded_file = None

    return run_react(
        message=message,
        uploaded_file=uploaded_file,
        history=history,
        chat=chat,
        tracer=tracer,
        enable_reasoning=enable_reasoning,
    )
