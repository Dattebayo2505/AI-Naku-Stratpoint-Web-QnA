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
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..models import Chunk

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


@dataclass(frozen=True)
class CaseResult:
    id: str
    expect: str
    axis: str | None
    rank: int | None        # 0-based rank of the gold slug; None when absent
    gold_score: float | None
    top1_score: float | None


def evaluate_case(case: GoldCase, chunks: list[Chunk]) -> CaseResult:
    """Score one case against the chunks retrieval returned for it.

    A page is split across chunks, so the gold slug can appear more than once;
    the best (first) occurrence is the one that counts.
    """
    rank: int | None = None
    gold_score: float | None = None
    if case.slug is not None:
        for i, c in enumerate(chunks):
            if c.slug == case.slug:
                rank, gold_score = i, c.score
                break
    return CaseResult(
        id=case.id,
        expect=case.expect,
        axis=case.axis,
        rank=rank,
        gold_score=gold_score,
        top1_score=chunks[0].score if chunks else None,
    )


def run_cases(
    cases: list[GoldCase],
    retrieve_fn: Callable[[str, int], list[Chunk]],
    k: int = 5,
) -> list[CaseResult]:
    """Evaluate every case. Retrieval is injected so metrics stay testable."""
    return [evaluate_case(c, retrieve_fn(c.q, k)) for c in cases]


def _hit(r: CaseResult, k: int) -> bool:
    return r.rank is not None and r.rank < k


def _retrieve_only(results: list[CaseResult]) -> list[CaseResult]:
    return [r for r in results if r.expect == EXPECT_RETRIEVE]


def hit_rate(results: list[CaseResult], k: int = 5) -> float:
    """Fraction of answerable cases whose gold page appears in the top k."""
    rows = _retrieve_only(results)
    return sum(_hit(r, k) for r in rows) / len(rows) if rows else 0.0


def hit_rate_by_axis(results: list[CaseResult], k: int = 5) -> dict[str, float]:
    """Hit rate split by phrasing. A pronoun-axis collapse is the signal that
    entity anchoring has regressed; the overall rate would only dip."""
    by_axis: dict[str, list[CaseResult]] = {}
    for r in _retrieve_only(results):
        by_axis.setdefault(r.axis or "untagged", []).append(r)
    return {axis: sum(_hit(r, k) for r in rows) / len(rows) for axis, rows in by_axis.items()}


def mrr(results: list[CaseResult], k: int = 5) -> float:
    """Mean reciprocal rank over answerable cases; a miss contributes 0."""
    rows = _retrieve_only(results)
    if not rows:
        return 0.0
    return sum(1.0 / (r.rank + 1) if _hit(r, k) else 0.0 for r in rows) / len(rows)


def divergent_pairs(
    results: list[CaseResult], cases: list[GoldCase], k: int = 5
) -> list[tuple[str, str]]:
    """Paraphrase pairs where exactly one side hits, as (base_id, twin_id).

    This is the legible form of an anchoring regression: an aggregate hit-rate
    dip says something broke, a divergent pair says which phrasing broke.
    """
    hits = {r.id: _hit(r, k) for r in results}
    out = []
    for c in cases:
        if not c.paraphrase_of:
            continue
        base, twin = c.paraphrase_of, c.id
        if base in hits and twin in hits and hits[base] != hits[twin]:
            out.append((base, twin))
    return out


@dataclass(frozen=True)
class Separation:
    gold_min: float | None
    gold_p25: float | None
    gold_median: float | None
    abstain_median: float | None
    abstain_p75: float | None
    abstain_max: float | None
    overlap_count: int
    abstain_total: int
    gold_total: int


def _quantile(values: list[float], q: float) -> float:
    """Nearest-rank quantile. Avoids interpolation so a reported cutoff is
    always a score some case actually produced."""
    ordered = sorted(values)
    idx = min(int(q * len(ordered)), len(ordered) - 1)
    return ordered[idx]


def separation(results: list[CaseResult], k: int = 5) -> Separation:
    """Compare gold-chunk scores against unanswerable-question top-1 scores.

    `overlap_count` — abstain cases scoring above the median gold chunk — is the
    number Session 4 hangs on. Only *hit* answerable cases contribute a gold
    score: a miss has no gold chunk to score, and a gold chunk ranked outside k
    was not actually retrieved.
    """
    gold_scores = [
        r.gold_score for r in results
        if r.expect == EXPECT_RETRIEVE and _hit(r, k) and r.gold_score is not None
    ]
    abstain_top1 = [
        r.top1_score for r in results
        if r.expect == EXPECT_ABSTAIN and r.top1_score is not None
    ]

    gold_median = statistics.median(gold_scores) if gold_scores else None
    overlap = (
        sum(1 for s in abstain_top1 if s > gold_median) if gold_scores and abstain_top1 else 0
    )

    return Separation(
        gold_min=min(gold_scores) if gold_scores else None,
        gold_p25=_quantile(gold_scores, 0.25) if gold_scores else None,
        gold_median=gold_median,
        abstain_median=statistics.median(abstain_top1) if abstain_top1 else None,
        abstain_p75=_quantile(abstain_top1, 0.75) if abstain_top1 else None,
        abstain_max=max(abstain_top1) if abstain_top1 else None,
        overlap_count=overlap,
        abstain_total=len(abstain_top1),
        gold_total=len(gold_scores),
    )
