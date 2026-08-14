"""Eval harness — one registry, one table, one exit code.

Each layer is a zero-arg callable returning a LayerResult. REGISTRY is the only
seam: a new eval layer is one function appended here plus one FLOORS entry, and
it shows up in the table with no other change.

FLOORS gates the CLI exit code (D3): the command exits non-zero when any
non-skipped layer falls below its floor. Floors are committed per-layer, never a
blanket 100% — an always-red command trains everyone to ignore it. below_floor
uses FLOORS.get(name, 1.0) so an unregistered name fails loud.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from stratpoint_rag.evaluation import guardrail_eval as ge
from stratpoint_rag.evaluation.cost_eval import layer as _cost_layer
from stratpoint_rag.evaluation.extraction_eval import layer as _extraction_layer
from stratpoint_rag.evaluation.trajectory_eval import layer as _trajectory_layer
from stratpoint_rag.evaluation.e2e_eval import layer as _e2e_layer
from stratpoint_rag.evaluation.judge_eval import layer as _judge_layer


@dataclass
class LayerResult:
    layer: str          # "unit" | "extraction" | "cost" | "trajectory" | "e2e" | "judge"
    name: str           # registry key, e.g. "guardrails/deterministic"
    total: int
    passed: int
    detail: str = ""
    skipped: bool = False

    @property
    def pass_rate(self) -> float:
        return (self.passed / self.total) if self.total else 0.0


# Committed per-layer floors. Every one below was set AFTER a real measurement,
# never guessed; the measurement and its date are recorded beside each group.
#
# guardrails/deterministic is 0.60, not a higher-looking round number: the
# measured baseline is 13/20 = 0.65 (see guardrail_eval module docstring D2 —
# the deterministic layer legitimately lets some off-topic cases through that
# policy labels "block"). A floor above the measured baseline would make this
# command permanently red, which is exactly the failure mode floors exist to
# avoid.
# Measured 2026-08-14 over 5 seeded proposal sessions: trajectory 5/5, e2e 5/5,
# judge 4/4 (mean 3.75/5; one judge call failed and left the denominator at 4).
# Floors sit one session below the observed rate — below_floor uses `<`, so with
# n=5 a single off-path session lands exactly on 0.80 and still passes. n is
# small; re-measure before treating these as tight.
FLOORS: dict[str, float] = {
    "guardrails/deterministic": 0.60,
    "trajectory/proposal-path": 0.80,
    "e2e/proposal-chain": 0.80,
    "judge/proposal-quality": 0.75,
    # Measured 2026-08-14 over 8 real RFPs: grounding 171/179 = 0.955,
    # quote arithmetic 8/8 = 1.000.
    #
    # All 8 ungrounded values are `target_platform`, and they are a true
    # positive rather than a metric artifact: rfp9 names none of
    # ios/android/web/desktop and the extractor returned all four, emitting the
    # byte-identical list ['Web','iOS','Android','Desktop'] for two unrelated
    # briefs while correctly returning [] for a third. That is boilerplate, not
    # a reading of the document, and platforms feed complexity — so an invented
    # platform inflates a real quote. The floor is set below the observed rate
    # rather than the failures being excused; fixing the extraction prompt is
    # what should move this number.
    "extraction/brief-grounding": 0.90,
    "cost/quote-arithmetic": 0.875,   # one of eight quotes may fail and still pass
}


def below_floor(r: LayerResult) -> bool:
    if r.skipped:
        return False
    return r.pass_rate < FLOORS.get(r.name, 1.0)


def _guardrail_deterministic() -> LayerResult:
    res = ge.run_guardrail_eval(use_nemo=False)
    return LayerResult(
        "unit", "guardrails/deterministic", res["total"], res["passed"],
        detail=f"{len(res['failures'])} off-policy",
    )


# New eval layers append their `layer` callable here; each is the only wiring
# needed beyond a FLOORS entry (see module docstring).
REGISTRY: list[Callable[[], LayerResult]] = [
    _guardrail_deterministic,
    _extraction_layer,
    _cost_layer,
    _trajectory_layer,
    _e2e_layer,
    _judge_layer,
]


def run_all() -> list[LayerResult]:
    return [fn() for fn in REGISTRY]


def format_table(results: list[LayerResult]) -> str:
    header = f"{'LAYER':<12} {'EVAL':<28} {'PASS':>8} {'RATE':>7} {'FLOOR':>7}  STATUS"
    lines = [header, "-" * len(header)]
    used_implicit_floor = False
    for r in results:
        floor = FLOORS.get(r.name)
        if floor is not None:
            floor_s = f"{floor:.2f}"
        else:
            # below_floor() gates unregistered names at 1.0 (see FLOORS.get
            # default above) — showing "-" here would let a FAIL row read as
            # self-contradictory ("FLOOR - STATUS FAIL"). Mark it as implicit
            # instead so the number and the verdict agree.
            floor_s = "1.00*"
            used_implicit_floor = True
        if r.skipped:
            status = "SKIP"
        elif below_floor(r):
            status = "FAIL"
        else:
            status = "ok"
        passes = f"{r.passed}/{r.total}"
        row = f"{r.layer:<12} {r.name:<28} {passes:>8} {r.pass_rate:>7.2f} {floor_s:>7}  {status}"
        # detail carries WHY, e.g. distinguishing "no seeded cases" from "2
        # stalled before the PDF (unscored)" — without it, very different
        # reasons for a row's number collapse into an identical-looking line and
        # a reader cannot tell "nothing ran" from "something ran and was
        # filtered out".
        if r.detail:
            row += f"  ({r.detail})"
        lines.append(row)
    if used_implicit_floor:
        lines.append("* implicit floor 1.00 — no committed floor yet")
    return "\n".join(lines)
