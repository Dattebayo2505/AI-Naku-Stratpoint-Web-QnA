from __future__ import annotations

import json
from pathlib import Path

from stratpoint_rag.evaluation import traces, e2e_eval as ee

FIXTURE = Path(__file__).parent / "fixtures" / "traces.jsonl"

def _load():
    return [json.loads(l) for l in FIXTURE.read_text().splitlines() if l.strip()]

def test_completed_session():
    sess = traces.load_sessions(_load())
    ok, _ = ee.session_completed(sess["good_1"])
    assert ok is True

def test_errored_proposal_not_completed():
    sess = traces.load_sessions(_load())
    ok, reason = ee.session_completed(sess["errored_3"])
    assert ok is False
    assert "error" in reason.lower()

def test_run_scores_only_proposal_sessions():
    res = ee.run_e2e_eval(_load())
    # good_1 completes; no_estimate_2 completes (PDF, no error — trajectory is a
    # SEPARATE concern); errored_3 fails. chat_only_4 not counted.
    assert res["total"] == 3
    assert res["passed"] == 2
