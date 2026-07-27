"""Public seam for the ReAct agent path.

The loop itself lives in `react.py`; the result models in `models.py`. This
module stays as the import point every caller already uses
(`from stratpoint_rag.agent.agent import ...`), and re-exports the models so
those imports keep resolving.
"""
from __future__ import annotations

from stratpoint_rag.agent.models import AgentResult, Link, Step
from stratpoint_rag.agent.react import run_react

__all__ = ["AgentResult", "Link", "Step", "run_agent"]


def run_agent(
    message: str,
    history: list[dict] | None = None,
    *,
    chat=None,
    enable_reasoning: bool = False,
) -> AgentResult:
    """Run one turn of the ReAct agent and return a structured AgentResult.

    `chat` is an injection seam for tests: a callable (messages, stop) -> str.
    Production passes None and react.py supplies the httpx-backed NIM client.
    """
    return run_react(
        message, history=history, chat=chat, enable_reasoning=enable_reasoning
    )
