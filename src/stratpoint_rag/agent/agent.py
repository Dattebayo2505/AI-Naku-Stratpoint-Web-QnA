"""Public seam for the ReAct agent path.

The loop itself lives in `react.py`; the result models in `models.py`. This
module stays as the import point callers use (`from stratpoint_rag.agent.agent import ...`),
and re-exports the models so those imports keep resolving.
"""
from __future__ import annotations

from stratpoint_rag.agent.models import AgentResult, Link, ProposalData, Step
from stratpoint_rag.agent.react import run_react
from stratpoint_rag.agent.tracer import AgentTracer
from stratpoint_rag.docparse import BriefRef

__all__ = ["AgentResult", "Link", "Step", "ProposalData", "run_agent"]


def run_agent(
    message: str,
    uploaded_file: str | list[dict] | None = None,
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
    """Run one turn of the ReAct agent and return a structured AgentResult.

    Args:
        message: User prompt or query string.
        uploaded_file: Legacy display label, passed through to the tracer only.
                       Uploads reach the loop as `briefs`, addressed by id.
                       Supports positional passing for backward compatibility if a list is passed.
        history: Conversation history list.
        chat: Injection seam for tests: callable (messages, stop) -> str.
        tracer: Telemetry tracer implementing AgentTracer ABC.
        enable_reasoning: Controls whether thoughts are surfaced in AgentResult.reasoning.
        briefs: Uploads resolved for this session (docparse `BriefRef`s). A
                non-empty list is what registers the brief-extraction tool and
                puts the attachment manifest in the loop's system prompt.
        names: `(client_name, project_name)` the visitor supplied this session.
        session_id: Scopes a generated proposal PDF on disk and in its download URL.
        proposal_mode: True when the visitor asked for a proposal/quote; False
                       casts the loop as document Q&A instead.

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
        briefs=briefs,
        names=names,
        session_id=session_id,
        proposal_mode=proposal_mode,
    )
