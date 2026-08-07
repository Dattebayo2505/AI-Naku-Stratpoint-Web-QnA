"""ReAct agent: reason/act loop orchestrating retrieval, brief parsing, proposal calculator,
and PDF generation tools, with pluggable telemetry and optional guardrails wrapping.
"""
from stratpoint_rag.agent.agent import AgentResult, Link, ProposalData, Step, run_agent
from stratpoint_rag.agent.contracts import (
    BriefParserInput,
    EstimationInput,
    EstimationResult,
    ExtractedRequirements,
    PDFGenerationResult,
    PhaseTimelineItem,
    ProposalPDFInput,
    RoleBreakdownItem,
)
from stratpoint_rag.agent.guardrail_agent import clear_memory, run_with_guardrails
from stratpoint_rag.agent.tracer import AgentTracer, ConsoleTracer, NoOpTracer

__all__ = [
    "run_agent",
    "run_with_guardrails",
    "clear_memory",
    "AgentResult",
    "Link",
    "Step",
    "ProposalData",
    "AgentTracer",
    "NoOpTracer",
    "ConsoleTracer",
    "BriefParserInput",
    "ExtractedRequirements",
    "EstimationInput",
    "EstimationResult",
    "RoleBreakdownItem",
    "PhaseTimelineItem",
    "ProposalPDFInput",
    "PDFGenerationResult",
]
