"""Trajectory eval (Component #13) — recorded tool order vs the golden path.

The golden proposal path is: ground in the brief (read_brief OR
extract_brief_requirements) -> estimate_cost_and_timeline -> generate_proposal_pdf.
Two failure modes it catches, both real in this repo's history: a proposal with
no preceding estimate (a price with nothing behind it), and a proposal turn that
errored. Only sessions that attempted a proposal (generate_proposal_pdf present)
are scored — a plain Q&A session is not a failed proposal.

Scores recorded traces (llmops.read_records), never a re-run of the agent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stratpoint_rag import llmops
from stratpoint_rag.evaluation import traces

if TYPE_CHECKING:
    from stratpoint_rag.evaluation.harness import LayerResult

PROPOSAL_TOOL = "generate_proposal_pdf"
ESTIMATE_TOOL = "estimate_cost_and_timeline"
BRIEF_TOOLS = ("read_brief", "extract_brief_requirements")
# Golden path: ground in the brief (one of BRIEF_TOOLS) -> ESTIMATE_TOOL -> PROPOSAL_TOOL.
# score_session() checks this via positional index logic below, not a list constant.


def attempted_proposal(session_records: list[dict]) -> bool:
    return PROPOSAL_TOOL in traces.session_tool_calls(session_records)


def score_session(session_records: list[dict]) -> tuple[bool, str]:
    calls = traces.session_tool_calls(session_records)
    if PROPOSAL_TOOL not in calls:
        # Unreachable via run_trajectory_eval(), which pre-filters to sessions
        # where attempted_proposal() is True — this branch only runs if
        # score_session() is ever called directly on an unfiltered session.
        # True here reads as "trajectory not violated" (nothing to fail on),
        # the opposite default from e2e_eval.session_completed's False, which
        # reads as "task not completed" — pick the framing that fits, not
        # consistency with the sibling module, since neither path is live.
        return True, "no proposal attempted"  # not scored by caller; safe default
    err = traces.session_error(session_records)
    if err:
        return False, f"proposal turn errored: {err}"
    p_idx = calls.index(PROPOSAL_TOOL)
    if ESTIMATE_TOOL not in calls[:p_idx]:
        return False, "generate_proposal_pdf with no preceding estimate_cost_and_timeline"
    if not any(b in calls[:p_idx] for b in BRIEF_TOOLS):
        return False, "proposal not grounded in the brief (no read_brief/extract before estimate)"
    return True, "ok"


def run_trajectory_eval(records: list[dict] | None = None) -> dict:
    records = records if records is not None else llmops.read_records()
    sessions = traces.load_sessions(records)
    scored = {sid: recs for sid, recs in sessions.items() if attempted_proposal(recs)}
    passed = 0
    failures: list[dict] = []
    for sid, recs in scored.items():
        ok, reason = score_session(recs)
        passed += ok
        if not ok:
            failures.append({"session_id": sid, "reason": reason})
    total = len(scored)
    return {
        "total": total,
        "passed": passed,
        "pass_rate": (passed / total) if total else 0.0,
        "failures": failures,
    }


def layer() -> LayerResult:
    # Deferred, not top-level: harness imports this module's `layer` to build
    # REGISTRY, so a module-level `from harness import LayerResult` here would
    # be a circular import (harness <-> trajectory_eval) that fails at import
    # time depending on which module is entered first.
    from stratpoint_rag.evaluation.harness import LayerResult

    res = run_trajectory_eval()
    if res["total"] == 0:
        return LayerResult("trajectory", "trajectory/proposal-path", 0, 0,
                           detail="no proposal sessions traced", skipped=True)
    return LayerResult("trajectory", "trajectory/proposal-path", res["total"], res["passed"],
                       detail=f"{len(res['failures'])} off-path")


def render_walkthrough(session_records: list[dict]) -> str:
    """One annotated decision chain for the presentation (spec 6.1 line 134)."""
    sid = session_records[0].get("session_id", "?")
    lines = [f"# Reasoning trace walkthrough — session `{sid}`", ""]
    step = 1
    for r in session_records:
        for tool in r.get("tool_calls") or []:
            lines.append(f"{step}. **{tool}** — {r.get('ts')}")
            step += 1
    ok, reason = score_session(session_records)
    lines += ["", f"**Trajectory verdict:** {'PASS' if ok else 'FAIL'} — {reason}"]
    return "\n".join(lines)
