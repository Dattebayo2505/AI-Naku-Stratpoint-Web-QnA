"""Scored retrieval eval over the gold set (roadmap Session 0).

Run `uv run python -m stratpoint_rag.rag.eval.run`. Needs a populated Chroma
store — build it with `uv run stratpoint-rag-ingest` first.

This reports scores, not just hit/miss, because three later roadmap sessions
need them: the BGE query prefix shifts every score (so it needs a before/after
baseline), and the relevance floor is a threshold that has to be calibrated
against a real distribution. The headline output is the separation report --
whether gold-chunk scores and unanswerable-question top-1 scores overlap at
all. If they do, a single global distance cutoff cannot work, and that is worth
knowing before it is built.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

GOLD = Path(__file__).with_name("gold.jsonl")

EXPECT_RETRIEVE = "retrieve"
EXPECT_ABSTAIN = "abstain"
_EXPECTS = {EXPECT_RETRIEVE, EXPECT_ABSTAIN}


class GoldSetError(Exception):
    """The gold file is malformed or inconsistent with the corpus."""


@dataclass(frozen=True)
class GoldCase:
    id: str
    q: str
    expect: str
    slug: str | None = None
    axis: str | None = None
    paraphrase_of: str | None = None


def _parse_rows(path: Path) -> list[dict]:
    rows = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise GoldSetError(f"{path}: line {lineno} is not valid JSON: {exc}") from exc
    return rows


def load_cases(path: Path = GOLD, known_slugs: set[str] | None = None) -> list[GoldCase]:
    """Parse and validate the gold file.

    `known_slugs` is the set of slugs present in the corpus; when given, every
    retrieve case must name one. Pass None to skip that check (unit tests, and
    any run where the corpus is not loaded).
    """
    cases = [
        GoldCase(
            id=r.get("id", ""),
            q=r.get("q", ""),
            expect=r.get("expect", ""),
            slug=r.get("slug"),
            axis=r.get("axis"),
            paraphrase_of=r.get("paraphrase_of"),
        )
        for r in _parse_rows(path)
    ]

    problems: list[str] = []
    seen: set[str] = set()
    for c in cases:
        if not c.id:
            problems.append(f"a case is missing 'id' (q={c.q!r})")
        elif c.id in seen:
            problems.append(f"{c.id}: duplicate id")
        seen.add(c.id)

        if c.expect not in _EXPECTS:
            problems.append(f"{c.id}: expect must be one of {sorted(_EXPECTS)}, got {c.expect!r}")
        elif c.expect == EXPECT_RETRIEVE and not c.slug:
            problems.append(f"{c.id}: expect=retrieve requires a 'slug'")
        elif c.expect == EXPECT_ABSTAIN and c.slug:
            problems.append(f"{c.id}: expect=abstain must not carry a 'slug' (got {c.slug!r})")

    for c in cases:
        if c.paraphrase_of and c.paraphrase_of not in seen:
            problems.append(f"{c.id}: paraphrase_of={c.paraphrase_of!r} is not a known case id")

    if known_slugs is not None:
        missing = sorted({c.slug for c in cases if c.slug and c.slug not in known_slugs})
        if missing:
            problems.append("slugs not present in the corpus: " + ", ".join(missing))

    if problems:
        raise GoldSetError(f"{path}:\n  " + "\n  ".join(problems))
    return cases
