"""Structured output models for the RAG chatbot (plan §5.1).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Citation(BaseModel):
    url: str = Field(..., description="The direct URL of the source page referenced")
    title: str = Field(..., description="The title of the source page referenced")


class GroundedAnswer(BaseModel):
    reasoning: str = Field(
        ...,
        description="Step-by-step thinking checking if the context contains the facts needed to answer, identifying gaps.",
    )
    answer: str = Field(
        ...,
        description="The grounded answer to the question using ONLY provided context. If is_grounded is false, explain what is missing.",
    )
    citations: list[Citation] = Field(
        ..., description="List of source URLs and titles used to build the answer."
    )
    is_grounded: bool = Field(
        ...,
        description="True if context contains sufficient information; False if insufficient or out of scope.",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score between 0.0 and 1.0 indicating grounding certainty.",
    )
