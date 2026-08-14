"""Extraction eval (Component #13) — is every extracted requirement in the brief?

Scores the requirements captured by `seed_cases.py` against the brief's own
content-word set. A value is *grounded* when the brief supports it; the metric
is the share of extracted values that are.

**Grounding, not recall.** This layer catches a requirement the model invented
and by construction cannot catch one it dropped — measuring recall needs a
labelled golden set, which this corpus does not have. The asymmetry is the right
way round for this product: an invented requirement is silently priced and
printed on a commercial document, while a dropped one is a gap the visitor can
see. Named `brief-grounding` rather than `extraction-accuracy` so the number is
not read as something it isn't.

Scores seeded cases, never a re-run of the extractor — the same contract as the
trace-backed layers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from stratpoint_rag.docparse.transcribe import _content_words

if TYPE_CHECKING:
    from stratpoint_rag.evaluation.harness import LayerResult

CASES_PATH = Path(__file__).parent / "cases" / "pipeline_runs.jsonl"

# Every list on ExtractedRequirements that makes a claim about the document.
# `complexity` is a Literal and `phase_timeline` is structured, so neither is a
# free-text claim that can be checked this way.
CLAIM_FIELDS = ("target_platform", "features", "constraints", "tech_stack")


def is_grounded(value: str, brief_words: set[str]) -> bool:
    """True when the brief supports this value.

    The bar is *anchoring* — at least one content word in common — not
    containment. A good extractor normalises ("4 months" -> "4-month launch
    timeline", "PCI-DSS" -> "PCI compliant payments"), and exact-match scoring
    would report that correct behaviour as a hallucination. That is the same
    miscount the guardrail dataset was fixed for, and the instruction there
    applies here: do not score correct behaviour as a failure.

    ponytail: one shared word is a deliberately low bar, so this measures
    "invented from nothing", not fidelity. A value that borrows one word from
    the brief and invents the rest passes. Tightening it needs a labelled set to
    calibrate against — see the module docstring.
    """
    words = _content_words(value)
    return bool(words & brief_words)


def score_case(case: dict[str, Any]) -> dict[str, Any]:
    """Grounded/ungrounded counts for one seeded brief."""
    brief_words = set(case.get("brief_words") or ())
    requirements = case.get("requirements") or {}

    total = 0
    grounded = 0
    ungrounded: list[str] = []
    for field in CLAIM_FIELDS:
        for value in requirements.get(field) or ():
            # An empty string is not a claim about the brief, so it is neither
            # grounded nor ungrounded — counting it either way moves the rate on
            # something the model did not actually assert.
            if not str(value).strip():
                continue
            total += 1
            if is_grounded(str(value), brief_words):
                grounded += 1
            else:
                ungrounded.append(str(value))

    return {"file": case.get("file"), "total": total,
            "grounded": grounded, "ungrounded": ungrounded}


def load_cases(path: Path | None = None) -> list[dict[str, Any]]:
    """Seeded pipeline runs, or [] when none have been generated."""
    path = path or CASES_PATH
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def run_extraction_eval(cases: Iterable[dict[str, Any]] | None = None) -> dict:
    cases = list(cases) if cases is not None else load_cases()
    total = 0
    passed = 0
    failures: list[dict[str, str]] = []
    for case in cases:
        res = score_case(case)
        total += res["total"]
        passed += res["grounded"]
        failures += [{"file": res["file"], "value": v} for v in res["ungrounded"]]
    return {
        "total": total,
        "passed": passed,
        "pass_rate": (passed / total) if total else 0.0,
        "failures": failures,
    }


def layer() -> LayerResult:
    # Deferred import: harness imports this module to build REGISTRY, so a
    # module-level import of LayerResult would be a circular import.
    from stratpoint_rag.evaluation.harness import LayerResult

    res = run_extraction_eval()
    if res["total"] == 0:
        return LayerResult("extraction", "extraction/brief-grounding", 0, 0,
                           detail="no seeded cases — run seed_cases first", skipped=True)
    return LayerResult("extraction", "extraction/brief-grounding",
                       res["total"], res["passed"],
                       detail=f"{len(res['failures'])} ungrounded")
