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

import os
from dataclasses import dataclass
from typing import Callable

from stratpoint_rag.evaluation import guardrail_eval as ge
from stratpoint_rag.evaluation.trajectory_eval import layer as _trajectory_layer
from stratpoint_rag.evaluation.e2e_eval import layer as _e2e_layer
from stratpoint_rag.evaluation.judge_eval import layer as _judge_layer


@dataclass
class LayerResult:
    layer: str          # "unit" | "trajectory" | "e2e" | "judge" | "live"
    name: str           # registry key, e.g. "guardrails/deterministic"
    total: int
    passed: int
    detail: str = ""
    skipped: bool = False

    @property
    def pass_rate(self) -> float:
        return (self.passed / self.total) if self.total else 0.0


# Committed per-layer floors. Guardrail floors are known; the three new layers
# (trajectory/e2e/judge) are added by their own tasks AFTER a first real
# measurement, never guessed here.
#
# guardrails/deterministic is 0.60, not a higher-looking round number: the
# measured baseline is 13/20 = 0.65 (see guardrail_eval module docstring D2 —
# the deterministic layer legitimately lets some off-topic cases through that
# policy labels "block"). A floor above the measured baseline would make this
# command permanently red, which is exactly the failure mode floors exist to
# avoid.
FLOORS: dict[str, float] = {
    "guardrails/deterministic": 0.60,
    "guardrails/end-to-end": 1.0,
    # "trajectory/proposal-path": <measured>,   # set after first seeded run
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


def _guardrail_end_to_end() -> LayerResult:
    if not ge.live_available():
        # live_available() gates on two independent conditions (API key +
        # importable nemoguardrails); mirror both checks here so the SKIP
        # detail tells a reader of the table *why* — "we never had a key" vs
        # "we have a key but can't actually call NeMo" are different facts,
        # and collapsing them back into one generic message would re-hide
        # exactly what live_available() was added to surface.
        if not os.getenv("NVIDIA_API_KEY"):
            detail = "no NVIDIA_API_KEY"
        else:
            detail = "nemoguardrails not installed"
        return LayerResult("live", "guardrails/end-to-end", 0, 0, detail=detail, skipped=True)

    # Installed and keyed is not the same as working: NeMo fails OPEN, so a
    # 401 or a Colang error would otherwise be scored as "NeMo allowed it" and
    # republished as a real measurement. Probe once and skip loudly instead.
    healthy, why = ge.nemo_health()
    if not healthy:
        return LayerResult(
            "live", "guardrails/end-to-end", 0, 0,
            detail=f"NeMo erroring: {why}", skipped=True,
        )
    res = ge.run_guardrail_eval(use_nemo=True)
    return LayerResult("live", "guardrails/end-to-end", res["total"], res["passed"])


# New eval layers append their `layer` callable here; each is the only wiring
# needed beyond a FLOORS entry (see module docstring).
REGISTRY: list[Callable[[], LayerResult]] = [
    _guardrail_deterministic,
    _guardrail_end_to_end,
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
        # detail carries WHY, e.g. distinguishing "no NVIDIA_API_KEY" from
        # "nemoguardrails not installed" on a SKIP row — without it, two very
        # different reasons for not running collapse into an identical-looking
        # line and a reader can't tell "not configured" from "not installed".
        if r.detail:
            row += f"  ({r.detail})"
        lines.append(row)
    if used_implicit_floor:
        lines.append("* implicit floor 1.00 — no committed floor yet")
    return "\n".join(lines)
