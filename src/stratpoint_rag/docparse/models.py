"""Result types for a transcription run."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["BriefRef", "PageResult", "TranscriptionResult"]


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
    # Set when the page parsed but was repaired on the way out — currently only
    # a collapsed degeneration loop. Stamped into the page's provenance comment
    # for the same reason ``pages_failed`` exists: a page the pipeline had to
    # edit must not read as a clean transcription.
    note: str | None = None


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


@dataclass(frozen=True)
class BriefRef:
    """A stored upload, resolved far enough for hop 2 and for the agent's
    attachment manifest — but *not* its contents.

    This is what an ``upload_id`` means once it has been looked up in a session:
    where the transcription lives, how much of it hop 1 managed to read, and the
    ``sha256`` that keys the hop-2 cache. The markdown itself is read lazily so
    building a manifest for the system prompt does not pull a 40k-character
    document into memory for every chat turn.
    """

    upload_id: str
    filename: str
    sha256: str
    markdown_path: str | None = None
    pages_total: int = 0
    pages_parsed: int = 0
    pages_failed: list[int] = field(default_factory=list)

    @property
    def transcribed(self) -> bool:
        """True once hop 1 has run and left an artifact on disk."""
        return self.markdown_path is not None
