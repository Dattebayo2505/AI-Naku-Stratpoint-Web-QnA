import json

import httpx
import respx

from stratpoint_rag.rag import answer as answer_mod
from stratpoint_rag.rag.models import Chunk

_NIM_URL = "https://integrate.api.nvidia.com/v1/chat/completions"


def _stub(monkeypatch):
    monkeypatch.setattr(answer_mod.config, "nvidia_api_key", lambda: "k")
    monkeypatch.setattr(
        answer_mod, "retrieve",
        lambda q, k: [Chunk(id="1", slug="s", url="https://stratpoint.com/s", title="t", text="ctx")],
    )


def _payload(content, reasoning_key=None, reasoning=None):
    msg = {"content": content}
    if reasoning_key is not None:
        msg[reasoning_key] = reasoning
    return {"choices": [{"message": msg}]}


@respx.mock
def test_enable_reasoning_sends_thinking_params_and_returns_reasoning(monkeypatch):
    _stub(monkeypatch)
    body = json.dumps({"answer": "A", "citations": [], "is_grounded": True, "confidence": 0.9})
    route = respx.post(_NIM_URL).mock(
        return_value=httpx.Response(200, json=_payload(body, "reasoning_content", "I checked the context."))
    )

    text, chunks, grounded, reasoning = answer_mod.answer_grounded("q", enable_reasoning=True)

    sent = json.loads(route.calls[0].request.content)
    assert sent["chat_template_kwargs"] == {"enable_thinking": True}
    assert "response_format" not in sent  # json_object mode suppresses reasoning
    assert reasoning == "I checked the context."
    assert grounded.answer == "A"


@respx.mock
def test_reasoning_off_omits_params_and_returns_none(monkeypatch):
    _stub(monkeypatch)
    body = json.dumps({"answer": "A", "citations": [], "is_grounded": True, "confidence": 0.9})
    route = respx.post(_NIM_URL).mock(return_value=httpx.Response(200, json=_payload(body)))

    text, chunks, grounded, reasoning = answer_mod.answer_grounded("q", enable_reasoning=False)

    sent = json.loads(route.calls[0].request.content)
    assert "chat_template_kwargs" not in sent
    assert sent["response_format"] == {"type": "json_object"}
    assert reasoning is None


@respx.mock
def test_markdown_fenced_json_still_parses(monkeypatch):
    """Reasoning-on drops json_object mode, so NIM often wraps JSON in a ```json
    fence. The answer must still parse to clean text, not fall back to raw."""
    _stub(monkeypatch)
    inner = {"answer": "We do cloud.", "citations": [], "is_grounded": True, "confidence": 1.0}
    fenced = "```json\n" + json.dumps(inner) + "\n```"
    respx.post(_NIM_URL).mock(
        return_value=httpx.Response(200, json=_payload(fenced, "reasoning_content", "thought"))
    )

    text, chunks, grounded, reasoning = answer_mod.answer_grounded("q", enable_reasoning=True)
    assert grounded is not None and grounded.answer == "We do cloud."
    assert text == "We do cloud."
    assert reasoning == "thought"


@respx.mock
def test_reasoning_read_falls_back_to_reasoning_key(monkeypatch):
    _stub(monkeypatch)
    body = json.dumps({"answer": "A", "citations": [], "is_grounded": True, "confidence": 0.9})
    # Some NIM builds may name it "reasoning" instead of "reasoning_content".
    respx.post(_NIM_URL).mock(
        return_value=httpx.Response(200, json=_payload(body, "reasoning", "alt-key thoughts"))
    )

    _, _, _, reasoning = answer_mod.answer_grounded("q", enable_reasoning=True)
    assert reasoning == "alt-key thoughts"
