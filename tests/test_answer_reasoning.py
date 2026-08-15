"""Reasoning on the RAG path is PROMPTED, not native.

NIM's endpoint for meta/llama-3.1-8b-instruct does not support enable_thinking,
so the model is asked for a 'Reasoning:' preamble ahead of the JSON object and
answer.py splits it off.
"""
import json

import httpx
import respx

from stratpoint_rag.rag import answer as answer_mod
from stratpoint_rag.rag.models import Chunk

_NIM_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
_BODY = {"answer": "A", "citations": [], "is_grounded": True, "confidence": 0.9}


def _stub(monkeypatch):
    monkeypatch.setattr(answer_mod.config, "nvidia_api_key", lambda: "k")
    monkeypatch.setattr(
        answer_mod,
        "retrieve",
        lambda q, k: [Chunk(id="1", slug="s", url="https://stratpoint.com/s", title="t", text="ctx")],
    )


def _payload(content):
    return {"choices": [{"message": {"content": content}}]}


@respx.mock
def test_reasoning_on_drops_json_mode_and_splits_the_preamble(monkeypatch):
    _stub(monkeypatch)
    content = "Reasoning: The context names our cloud services.\n" + json.dumps(_BODY)
    route = respx.post(_NIM_URL).mock(return_value=httpx.Response(200, json=_payload(content)))

    text, chunks, grounded, reasoning = answer_mod.answer_grounded("q", enable_reasoning=True)

    sent = json.loads(route.calls[0].request.content)
    assert "response_format" not in sent
    assert "chat_template_kwargs" not in sent  # the native path is gone
    assert reasoning == "The context names our cloud services."
    assert grounded.answer == "A"
    assert text == "A"


@respx.mock
def test_reasoning_off_keeps_json_mode_and_returns_none(monkeypatch):
    _stub(monkeypatch)
    route = respx.post(_NIM_URL).mock(
        return_value=httpx.Response(200, json=_payload(json.dumps(_BODY)))
    )

    text, chunks, grounded, reasoning = answer_mod.answer_grounded("q", enable_reasoning=False)

    sent = json.loads(route.calls[0].request.content)
    assert sent["response_format"] == {"type": "json_object"}
    assert "chat_template_kwargs" not in sent
    assert reasoning is None
    assert grounded.answer == "A"


@respx.mock
def test_reasoning_on_selects_the_reasoning_variant(monkeypatch):
    _stub(monkeypatch)
    route = respx.post(_NIM_URL).mock(
        return_value=httpx.Response(200, json=_payload(json.dumps(_BODY)))
    )
    answer_mod.answer_grounded("q", enable_reasoning=True)

    sent = json.loads(route.calls[0].request.content)
    assert "Reasoning:" in sent["messages"][0]["content"]


@respx.mock
def test_fenced_json_after_the_preamble_still_parses(monkeypatch):
    """Without json_object mode NIM often wraps the body in a ```json fence."""
    _stub(monkeypatch)
    content = "Reasoning: Checked.\n```json\n" + json.dumps(_BODY) + "\n```"
    respx.post(_NIM_URL).mock(return_value=httpx.Response(200, json=_payload(content)))

    text, chunks, grounded, reasoning = answer_mod.answer_grounded("q", enable_reasoning=True)
    assert grounded is not None and grounded.answer == "A"
    assert reasoning == "Checked."


@respx.mock
def test_missing_preamble_still_parses_the_json(monkeypatch):
    """Non-compliance must degrade to 'no reasoning', not to a parse failure."""
    _stub(monkeypatch)
    respx.post(_NIM_URL).mock(
        return_value=httpx.Response(200, json=_payload(json.dumps(_BODY)))
    )

    text, chunks, grounded, reasoning = answer_mod.answer_grounded("q", enable_reasoning=True)
    assert grounded is not None and grounded.answer == "A"
    assert reasoning is None


def test_split_reasoning_leaves_plain_json_untouched():
    raw = json.dumps(_BODY)
    body, reasoning = answer_mod._split_reasoning(raw)
    assert body == raw and reasoning is None


# ── null content from the endpoint ─────────────────────────────────────────
#
# `content` is present but null on a filtered or empty completion. The helper
# did a bare `s.strip()` (its twin in docparse/extract.py has always guarded
# with `(s or "")`), so this raised AttributeError, the surrounding except
# swallowed it, and `answer=None` came back — which then failed AgentResult
# validation on the non-agent path and surfaced to the user as a 502.


def test_strip_code_fences_tolerates_none():
    assert answer_mod._strip_code_fences(None) == ""


@respx.mock
def test_null_content_degrades_to_empty_text_not_an_exception(monkeypatch):
    _stub(monkeypatch)
    respx.post(_NIM_URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": None}}]})
    )

    text, chunks, grounded, reasoning = answer_mod.answer_grounded("q")

    assert text == ""          # a str, never None
    assert grounded is None    # parse failed, as it should
    assert chunks              # retrieval still happened


@respx.mock
def test_missing_content_key_degrades_the_same_way(monkeypatch):
    _stub(monkeypatch)
    respx.post(_NIM_URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {}}]})
    )

    text, _, grounded, _ = answer_mod.answer_grounded("q")
    assert text == ""
    assert grounded is None


@respx.mock
def test_echoed_schema_before_json_object_parses_successfully(monkeypatch):
    """When the model outputs the schema definition before the actual answer JSON."""
    _stub(monkeypatch)
    schema_def = {
        "$defs": {"Citation": {"properties": {"url": {"type": "string"}, "title": {"type": "string"}}}},
        "properties": {"answer": {"type": "string"}, "citations": {"type": "array"}, "is_grounded": {"type": "boolean"}, "confidence": {"type": "number"}},
        "required": ["answer", "citations", "is_grounded", "confidence"],
        "title": "GroundedAnswer",
        "type": "object"
    }
    actual_answer = {
        "answer": "Here are 5 jobs:\n\n| Role | Location |\n| --- | --- |\n| Dev | Manila |",
        "citations": [{"url": "https://stratpoint.com/careers/", "title": "Careers"}],
        "is_grounded": True,
        "confidence": 0.95
    }
    content = f"Reasoning: Found jobs in context.\n{json.dumps(schema_def)}\n{json.dumps(actual_answer)}"
    respx.post(_NIM_URL).mock(return_value=httpx.Response(200, json=_payload(content)))

    text, chunks, grounded, reasoning = answer_mod.answer_grounded("q", enable_reasoning=True)

    assert grounded is not None
    assert "Here are 5 jobs:" in grounded.answer
    assert "| Role | Location |" in grounded.answer
    assert grounded.is_grounded is True
    assert grounded.confidence == 0.95
    assert len(grounded.citations) == 1
    assert reasoning == "Found jobs in context."
    assert "Here are 5 jobs:" in text


@respx.mock
def test_reasoning_ending_with_json_output_label_is_cleaned(monkeypatch):
    """When the reasoning text ends with 'JSON Output:' before the JSON objects."""
    _stub(monkeypatch)
    actual_answer = {
        "answer": "| Role | Setup |\n| --- | --- |\n| Data Eng | Remote |",
        "citations": [{"url": "https://stratpoint.com/careers/", "title": "Careers"}],
        "is_grounded": True,
        "confidence": 0.8
    }
    content = f"Reasoning: Analyzed the context.\n\nJSON Output:\n{json.dumps(actual_answer)}"
    respx.post(_NIM_URL).mock(return_value=httpx.Response(200, json=_payload(content)))

    text, chunks, grounded, reasoning = answer_mod.answer_grounded("q", enable_reasoning=True)

    assert grounded is not None
    assert "| Role | Setup |" in grounded.answer
    assert reasoning == "Analyzed the context."


@respx.mock
def test_verbatim_user_multi_object_payload_with_table(monkeypatch):
    """Verbatim test case reported by the user containing schema + answer table."""
    _stub(monkeypatch)
    content = (
        "Reasoning: The provided context includes various links and information about Stratpoint's services. "
        "To answer the question, I will extract relevant information from the provided context.\n\n"
        "JSON Output:\n"
        "{\n"
        '  "$defs": {\n'
        '    "Citation": {\n'
        '      "properties": {\n'
        '        "url": {"type": "string"},\n'
        '        "title": {"type": "string"}\n'
        "      }\n"
        "    }\n"
        "  },\n"
        '  "properties": {\n'
        '    "answer": {"type": "string"},\n'
        '    "citations": {"type": "array"},\n'
        '    "is_grounded": {"type": "boolean"},\n'
        '    "confidence": {"type": "number"}\n'
        "  },\n"
        '  "title": "GroundedAnswer",\n'
        '  "type": "object"\n'
        "}\n"
        "{\n"
        '  "answer": "Here are 5 job listings in table format:\\n\\n| Role | Work Setup | Location | Job Code | Application Link |\\n| --- | --- | --- | --- | --- |\\n| Data Engineer | Partially Remote | Mandaluyong, Metro Manila | `strt0382` | [Apply Now](https://stratpoint.hire.trakstar.com/jobs/fk0px4x/) |\\n",\n'
        '  "citations": [\n'
        '    {"url": "https://stratpoint.com/data-engineering/", "title": "Data Engineering"}\n'
        "  ],\n"
        '  "is_grounded": true,\n'
        '  "confidence": 0.8\n'
        "}"
    )
    respx.post(_NIM_URL).mock(return_value=httpx.Response(200, json=_payload(content)))

    text, chunks, grounded, reasoning = answer_mod.answer_grounded("q", enable_reasoning=True)

    assert grounded is not None
    assert "| Role | Work Setup | Location | Job Code | Application Link |" in grounded.answer
    assert "[Apply Now](https://stratpoint.hire.trakstar.com/jobs/fk0px4x/)" in grounded.answer
    assert grounded.is_grounded is True
    assert grounded.confidence == 0.8
    assert len(grounded.citations) == 1
    assert "JSON Output:" not in (reasoning or "")
    assert "| Role | Work Setup |" in text
    assert "Sources used:" in text


