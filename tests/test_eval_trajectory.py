from __future__ import annotations

import json
from pathlib import Path

from stratpoint_rag.evaluation import traces, trajectory_eval as te

FIXTURE = Path(__file__).parent / "fixtures" / "traces.jsonl"

def _load():
    return [json.loads(l) for l in FIXTURE.read_text().splitlines() if l.strip()]

def test_load_sessions_groups_and_orders():
    sess = traces.load_sessions(_load())
    assert set(sess) == {"good_1", "no_estimate_2", "errored_3", "chat_only_4"}
    assert traces.session_tool_calls(sess["good_1"]) == [
        "read_brief", "extract_brief_requirements", "estimate_cost_and_timeline", "generate_proposal_pdf",
    ]

def test_good_session_passes():
    sess = traces.load_sessions(_load())
    ok, _ = te.score_session(sess["good_1"])
    assert ok is True

def test_proposal_without_estimate_fails():
    sess = traces.load_sessions(_load())
    ok, reason = te.score_session(sess["no_estimate_2"])
    assert ok is False
    assert "estimate" in reason.lower()

def test_errored_proposal_fails():
    sess = traces.load_sessions(_load())
    ok, reason = te.score_session(sess["errored_3"])
    assert ok is False
    assert "error" in reason.lower()

def test_run_only_scores_proposal_sessions():
    res = te.run_trajectory_eval(_load())
    # good_1, no_estimate_2, errored_3 attempted a proposal; chat_only_4 did not
    assert res["total"] == 3
    assert res["passed"] == 1

def test_walkthrough_is_markdown():
    sess = traces.load_sessions(_load())
    md = te.render_walkthrough(sess["good_1"])
    assert md.startswith("#")
    assert "generate_proposal_pdf" in md
