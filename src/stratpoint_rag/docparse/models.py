"""Result types for a transcription run."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["PageResult", "TranscriptionResult"]


@dataclass(frozen=True)
class PageResult:
    """One page's outcome. ``number`` is 1-based and exact — the whole
    ``pages_failed`` accounting hangs off it."""

    number: int
    markdown: str
    source: str  # "text" | "vision"
    failed: bool = False
    failure_reason: str | None = None
    usage: dict | None = None


@dataclass(frozen=True)
class TranscriptionResult:
    """The hop-1 artifact plus the provenance the UI surfaces.

    ``pages_failed`` is the reason this shape exists: without it, a brief where
    vision choked on 6 of 20 pages returns the same shape as a clean one, and
    the agent presents it with equal confidence — which cuts against the
    grounded-answer premise the rest of the project rests on.
    """

    markdown: str
    source_file: str
    sha256: str
    pages_total: int
    pages_parsed: int
    pages_failed: list[int] = field(default_factory=list)
    pages_via_vision: int = 0
    truncated: bool = False
    usage: dict = field(default_factory=dict)
