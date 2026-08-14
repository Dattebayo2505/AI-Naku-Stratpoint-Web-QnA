"""End-to-end eval (Component #13) — did the task actually finish.

Task success = a proposal session in which generate_proposal_pdf ran with no
error. The dollar-sanity ("non-zero total, >=1 line item") needs no separate
check: pdf_gen RAISES on an empty estimate or a render failure, so a clean
generate_proposal_pdf in the trace already implies a real quote. A tool that
raised shows up here as a non-null error.

Distinct from trajectory: this measures completion, not path correctness.
Scores recorded traces, never a re-run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stratpoint_rag import llmops
from stratpoint_rag.evaluation import traces

if TYPE_CHECKING:
    from stratpoint_rag.evaluation.harness import LayerResult

PROPOSAL_TOOL = "generate_proposal_pdf"

def attempted_proposal(session_records: list[dict]) -> bool:
    return PROPOSAL_TOOL in traces.session_tool_calls(session_records)

def session_completed(session_records: list[dict]) -> tuple[bool, str]:
    if not attempted_proposal(session_records):
        # Unreachable via run_e2e_eval(), which pre-filters to sessions where
        # attempted_proposal() is True — this branch only runs if
        # session_completed() is ever called directly on an unfiltered
        # session. False here reads as "task not completed" (no task means no
        # success), the opposite default from trajectory_eval.score_session's
        # True — pick the framing that fits this layer's own semantics
        # (completion, not path correctness), not consistency with the
        # sibling module, since neither path is live.
        return False, "no proposal attempted"
    err = traces.session_error(session_records)
    if err:
        return False, f"errored: {err}"
    return True, "ok"

def run_e2e_eval(records: list[dict] | None = None) -> dict:
    records = records if records is not None else llmops.read_records()
    sessions = traces.load_sessions(records)
    scored = {sid: recs for sid, recs in sessions.items() if attempted_proposal(recs)}
    passed = 0
    failures: list[dict] = []
    for sid, recs in scored.items():
        ok, reason = session_completed(recs)
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
    # be a circular import (harness <-> e2e_eval) that fails at import time
    # depending on which module is entered first.
    from stratpoint_rag.evaluation.harness import LayerResult

    res = run_e2e_eval()
    if res["total"] == 0:
        return LayerResult("e2e", "e2e/proposal-chain", 0, 0,
                           detail="no proposal sessions traced", skipped=True)
    return LayerResult("e2e", "e2e/proposal-chain", res["total"], res["passed"],
                       detail=f"{len(res['failures'])} incomplete")
