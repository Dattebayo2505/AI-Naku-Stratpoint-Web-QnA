"""Read a client name / project name *suggestion* out of a transcription.

The names are not part of ``ExtractedRequirements`` and never will be — see
``docparse/schema.py``. But "the parser must not invent them" is not the same as
"the system can never know them": the transcript very often states them outright,
and offering what it says is genuinely useful.

So this module answers exactly one question: **what, if anything, did the
document claim its client and project were called?** The answer is a suggestion
and nothing more. It is shown to the visitor, attributed to the document, and
becomes a usable value only once the visitor affirms it. Silence is not consent.

**This is deliberately a scan, not a model call.** An LLM asked "what is the
client called?" over attacker-controllable text opens a channel whose output
then gets printed on a commercial document; a labelled-line scan can only ever
echo a line the document really contains, is deterministic, costs nothing, and
is testable offline. Markdown decoration — links especially — is stripped, so a
planted ``[Northwind](http://evil/x.pdf)`` cannot smuggle a URL into a heading.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["NameSuggestion", "suggest_names"]

# Bounds a heading-sized value. Long enough for "Department of Information and
# Communications Technology", short enough that a paragraph is not a name.
_MIN_CHARS = 2
_MAX_CHARS = 80

_CLIENT_LABELS = r"client(?:\s+name)?|customer|prepared\s+for|submitted\s+to|for"
_PROJECT_LABELS = r"project(?:\s+name|\s+title)?|engagement|initiative"

_CLIENT_LINE = re.compile(
    rf"^[ \t>*_#|-]*(?:{_CLIENT_LABELS})[ \t]*[:–-][ \t]*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)
_PROJECT_LINE = re.compile(
    rf"^[ \t>*_#|-]*(?:{_PROJECT_LABELS})[ \t]*[:–-][ \t]*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)

_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_DECORATION = re.compile(r"[*_`~#]")

# Values that are labels for "we did not fill this in".
_PLACEHOLDERS = {
    "tbd", "tba", "n/a", "na", "none", "unknown", "xxx", "todo",
    "client", "customer", "project", "insert name here",
}


@dataclass(frozen=True)
class NameSuggestion:
    """What the document said. Never what the visitor confirmed."""

    client_name: str | None = None
    project_name: str | None = None

    @property
    def is_empty(self) -> bool:
        return self.client_name is None and self.project_name is None


def _clean(value: str) -> str | None:
    """Reduce one captured line to a usable name, or None."""
    text = _MD_LINK.sub(r"\1", value)
    text = _MD_DECORATION.sub("", text)
    # A label line often carries the next field on the same row after a pipe or
    # a double space; keep only the first field.
    text = text.split("|")[0]
    text = text.strip().strip(".,;:").strip()

    if not (_MIN_CHARS <= len(text) <= _MAX_CHARS):
        return None
    if text.casefold() in _PLACEHOLDERS:
        return None
    # A "name" with no letter in it is a date, a number, or punctuation.
    if not any(ch.isalpha() for ch in text):
        return None
    return text


def _first(pattern: re.Pattern, markdown: str) -> str | None:
    for m in pattern.finditer(markdown or ""):
        cleaned = _clean(m.group(1))
        if cleaned:
            return cleaned
    return None


def suggest_names(markdown: str) -> NameSuggestion:
    """Scan a transcription for a stated client and project name.

    Returns the first plausible value for each. First, not best: briefs put the
    cover-page identification up top, and a "best" heuristic over a document
    this varied would be guessing dressed as ranking.
    """
    return NameSuggestion(
        client_name=_first(_CLIENT_LINE, markdown),
        project_name=_first(_PROJECT_LINE, markdown),
    )
