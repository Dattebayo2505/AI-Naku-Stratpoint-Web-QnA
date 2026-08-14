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
