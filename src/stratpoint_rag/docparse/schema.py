"""The hop-2 output contract.

``ExtractedRequirements`` is the parser's **sworn statement about what the
document contained**. Everything in it is document-derived and therefore
attacker-controllable; nothing in it is human-confirmed. That distinction is
the whole reason this shape looks the way it does:

- **There is no ``client_name`` and no ``project_name``.** A required field is
  an instruction to hallucinate — the old stub literally defaulted to
  ``"Acme Innovations"``. A visitor-supplied name is a different kind of fact
  (human-confirmed) and lives on ``ProposalPDFInput`` / session state instead.
  Merging the two back into one field would make "the brief said it" and "the
  visitor typed it" indistinguishable, and it would do so invisibly, because
  the shape would stay valid.
- **``complexity`` is a ``Literal``, not a ``str``.** The model returns
  ``"moderate"`` and ``"Medium-High"`` given the chance; the boundary rejects
  those rather than passing them to the estimator, which branches on the exact
  strings.
- **The provenance fields are copied from hop 1's run, never asked of the
  LLM.** ``pages_failed`` is what stops a brief where vision choked on 6 of 20
  pages from being presented with the same confidence as a clean one.
- **``extraction_notes`` is the only free-text field the model controls**,
  which makes it the one channel injected document content can travel through.
  It is length-capped on both axes for that reason.

This model lives in ``docparse`` — the package that produces it — and is
re-exported from ``agent.contracts`` so the proposal tools' existing imports
keep resolving. The dependency runs agent -> docparse and must not be inverted.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

__all__ = [
    "COMPLEXITY_ORDER",
    "MAX_NOTES",
    "MAX_NOTE_CHARS",
    "ExtractedRequirements",
]

# low < medium < high. The merge takes max() over this ordering: a brief whose
# hardest 5-page group is "high" is a high-complexity brief, and averaging would
# quietly under-price it.
COMPLEXITY_ORDER: tuple[str, ...] = ("low", "medium", "high")

MAX_NOTES = 8
MAX_NOTE_CHARS = 200


class ExtractedRequirements(BaseModel):
    """Structured requirements extracted from a transcribed client brief."""

    target_platform: list[str] = Field(
        default_factory=list,
        description="Target platforms stated in the brief (e.g. Web, iOS, Android).",
    )
    features: list[str] = Field(
        default_factory=list, description="Features the brief asks for."
    )
    constraints: list[str] = Field(
        default_factory=list,
        description="Constraints stated in the brief (timeline, compliance, budget).",
    )
    tech_stack: list[str] = Field(
        default_factory=list,
        description="Technologies the brief names as required or preferred.",
    )
    complexity: Literal["low", "medium", "high"] = Field(
        "medium", description="Overall complexity assessment."
    )

    currency_symbol: str = Field("$", description="Detected currency symbol ('$' or '₱').")
    currency_code: str = Field("USD", description="Detected currency code ('USD' or 'PHP').")
    phase_timeline: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Explicit project phases or milestones stated in the document.",
    )

    # ── provenance: copied from hop 1, never model-supplied ──────────────
    source_markdown_path: str | None = Field(
        None, description="Path to the hop-1 transcription this was read from."
    )
    pages_total: int = Field(0, description="Pages in the source document.")
    pages_parsed: int = Field(0, description="Pages hop 1 transcribed successfully.")
    pages_failed: list[int] = Field(
        default_factory=list, description="1-based page numbers hop 1 could not read."
    )
    extraction_notes: list[str] = Field(
        default_factory=list,
        description="Honest gaps, e.g. 'no timeline stated'. Length-capped.",
    )


ExtractedRequirements.model_rebuild()

