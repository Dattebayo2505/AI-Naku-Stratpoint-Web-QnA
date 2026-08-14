from __future__ import annotations

import pytest

from stratpoint_rag.evaluation import judge_eval as je


def test_html_to_text_strips_style_keeps_body():
    html = (
        "<html><head><style>.foo{color:red;font-size:13.5px}</style></head>"
        "<body><h1>Acme Corp Proposal</h1><p>4 roles, PHP 1.2M, 16 weeks.</p></body></html>"
    )
    text = je._html_to_text(html)
    assert ".foo" not in text
    assert "color:red" not in text
    assert "Acme Corp Proposal" in text
    assert "4 roles, PHP 1.2M, 16 weeks." in text


def test_prompt_contains_rubric_and_json_instruction():
    p = je.build_judge_prompt("A proposal for a mobile app, 4 roles, PHP 1.2M, 16 weeks.")
    assert "score" in p.lower()
    assert "json" in p.lower()


def test_parse_verdict_ok():
    v = je.parse_verdict('{"score": 4, "rationale": "clear scope, sane pricing"}')
    assert v["score"] == 4
    assert "rationale" in v


def test_parse_verdict_strips_code_fence():
    v = je.parse_verdict('```json\n{"score": 5, "rationale": "excellent"}\n```')
    assert v["score"] == 5


def test_parse_verdict_rejects_out_of_range():
    with pytest.raises(ValueError):
        je.parse_verdict('{"score": 9, "rationale": "x"}')


def test_parse_verdict_rejects_garbage():
    with pytest.raises(ValueError):
        je.parse_verdict("not json at all")


def test_parse_verdict_handles_literal_braces_in_rationale():
    v = je.parse_verdict('reasoning...\n{"score": 4, "rationale": "uses a {placeholder} token"}')
    assert v["score"] == 4


def test_parse_verdict_skips_earlier_json_looking_reasoning():
    v = je.parse_verdict('I considered {"a": 1} first.\n{"score": 5, "rationale": "ok"}')
    assert v["score"] == 5


@pytest.mark.integration
def test_judge_live_scores_a_proposal():
    if not je.live_available():
        pytest.skip("no NVIDIA_API_KEY")
    v = je.judge_proposal("Proposal: build a mobile app. 4 roles. PHP 1,200,000. 16 weeks. Line items included.")
    assert 1 <= v["score"] <= 5


def test_sample_proposals_reads_the_configured_proposal_dir(tmp_path, monkeypatch):
    """The judge must look where the app actually writes.

    It hardcoded `<repo>/data/proposals`, ignoring PROPOSAL_DIR. In the
    container the API writes to /app/proposals, so the judge looked at a
    directory nothing ever wrote to and reported SKIP — while a stale copy in
    the read-only ./data mount could make it report a score for proposals the
    running app had not produced.
    """
    root = tmp_path / "proposals" / "sess1"
    root.mkdir(parents=True)
    (root / "q.html").write_text(
        "<style>.x{color:red}</style><h1>Quote</h1><p>Discovery phase</p>",
        encoding="utf-8",
    )
    monkeypatch.setenv("PROPOSAL_DIR", str(tmp_path / "proposals"))

    samples = je._sample_proposals()

    assert len(samples) == 1
    assert "Discovery phase" in samples[0]
    assert "color:red" not in samples[0]


def test_a_judge_reply_without_json_is_retried_once(monkeypatch):
    """The 8B model ignores the JSON instruction ~30% of the time.

    Measured live on three real proposals: two returned parseable JSON, one
    answered in prose and was counted as a failed call. The request itself
    succeeded in 5.3s — this is prompt adherence, not the network, so a retry
    is the cheap fix. json_object mode is NOT the fix here: the prompt asks for
    step-by-step reasoning before the JSON, which that mode forbids (tried,
    measured at 100% failure, reverted).
    """
    replies = iter(["I think this proposal is quite good, really.",
                    '{"score": 4, "rationale": "solid"}'])
    calls = {"n": 0}

    class _Resp:
        def __init__(self, text):
            self._text = text

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": self._text}}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        return _Resp(next(replies))

    monkeypatch.setattr(je.httpx, "post", fake_post)

    verdict = je.judge_proposal("<h1>Quote</h1>")

    assert verdict["score"] == 4
    assert calls["n"] == 2, "the unparseable first reply should have been retried"


def test_a_judge_that_never_returns_json_still_raises(monkeypatch):
    """Two prose replies is a real failure — it must not be smoothed over."""
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "no json here at all"}}]}

    monkeypatch.setattr(je.httpx, "post", lambda *a, **k: _Resp())

    with pytest.raises(ValueError):
        je.judge_proposal("<h1>Quote</h1>")
