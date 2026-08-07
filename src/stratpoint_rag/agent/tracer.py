"""Observability tracer interface and default implementations.

Provides a pluggable tracer hook interface (`AgentTracer` ABC) so telemetry,
tool calls, errors, and timing can be logged without hardcoding any specific
observability SDK (e.g. LangSmith, Phoenix, MLflow).
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

log = logging.getLogger(__name__)


class AgentTracer(ABC):
    """Abstract base class for ReAct agent tracing & telemetry."""

    def on_agent_start(
        self, user_input: str, uploaded_file: str | None = None
    ) -> None:
        """Called when the ReAct loop starts a new run."""
        pass

    def on_thought(self, thought: str) -> None:
        """Called when the agent produces a reasoning step ('Thought: ...')."""
        pass

    @abstractmethod
    def on_tool_start(self, tool_name: str, tool_input: Any) -> None:
        """Called immediately before executing a tool."""
        pass

    @abstractmethod
    def on_tool_end(self, tool_name: str, tool_output: Any) -> None:
        """Called immediately after a tool executes successfully."""
        pass

    @abstractmethod
    def on_error(self, tool_name: str, error: Exception | str) -> None:
        """Called when a tool fails or raises an error."""
        pass

    def on_agent_end(self, result: Any) -> None:
        """Called when the ReAct loop finishes and returns an AgentResult."""
        pass


class NoOpTracer(AgentTracer):
    """Default no-op tracer that silently ignores all events."""

    def on_tool_start(self, tool_name: str, tool_input: Any) -> None:
        pass

    def on_tool_end(self, tool_name: str, tool_output: Any) -> None:
        pass

    def on_error(self, tool_name: str, error: Exception | str) -> None:
        pass


class ConsoleTracer(AgentTracer):
    """Simple tracer implementation that logs agent events to stdout/logging."""

    def on_agent_start(
        self, user_input: str, uploaded_file: str | None = None
    ) -> None:
        file_msg = f" (uploaded: {uploaded_file})" if uploaded_file else ""
        log.info("[AgentTracer] Agent started: '%s'%s", user_input, file_msg)

    def on_thought(self, thought: str) -> None:
        log.info("[AgentTracer] Thought: %s", thought)

    def on_tool_start(self, tool_name: str, tool_input: Any) -> None:
        log.info("[AgentTracer] Tool Start -> %s(input=%r)", tool_name, tool_input)

    def on_tool_end(self, tool_name: str, tool_output: Any) -> None:
        log.info("[AgentTracer] Tool End -> %s output summary: %s", tool_name, str(tool_output)[:100])

    def on_error(self, tool_name: str, error: Exception | str) -> None:
        log.error("[AgentTracer] Tool Error -> %s: %s", tool_name, error)

    def on_agent_end(self, result: Any) -> None:
        log.info("[AgentTracer] Agent ended successfully.")


_default_tracer: AgentTracer = NoOpTracer()


def get_default_tracer() -> AgentTracer:
    """Get the current global default tracer."""
    return _default_tracer


def set_default_tracer(tracer: AgentTracer) -> None:
    """Set the global default tracer."""
    global _default_tracer
    _default_tracer = tracer
