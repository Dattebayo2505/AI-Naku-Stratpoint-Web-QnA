"""NimTextClient — the hop-2 wire format.

Hop 2's reply is machine-parsed with no prose preamble to preserve, so unlike
``rag/answer.py``'s reasoning path this one keeps json_object mode. It also runs
on LLM_TIMEOUT rather than VISION_TIMEOUT: a handful of ordinary text calls on
the request thread, not twenty image calls on a pool.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from stratpoint_rag.docparse import config
from stratpoint_rag.docparse.nim import NimTextClient

URL = "https://integrate.api.nvidia.com/v1/chat/completions"

BODY = json.dumps(
    {
        "target_platform": ["Web"],
        "features": ["SSO"],
        "constraints": [],
        "tech_stack": [],
        "complexity": "medium",
        "extraction_notes": [],
    }
)


_DEFAULT_USAGE = {"total_tokens": 42}


def _ok(content=BODY, usage=_DEFAULT_USAGE):
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}], "usage": usage},
    )


@pytest.fixture(autouse=True)
def _key(request, monkeypatch):
    """A dummy key for the mocked tests — but never for the live one, which
    would then 401 against a real endpoint and look like a broken payload."""
    if request.node.get_closest_marker("integration"):
        return
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")


@respx.mock
def test_returns_the_content_and_usage():
    respx.post(URL).mock(return_value=_ok())

    text, usage = NimTextClient().complete("system", "user")

    assert json.loads(text)["features"] == ["SSO"]
    assert usage["total_tokens"] == 42


@respx.mock
def test_the_instructions_go_in_a_system_message():
    """Same rule as the vision client: rules beside the payload get echoed back
    into the output as though they were part of the document."""
    route = respx.post(URL).mock(return_value=_ok())

    NimTextClient().complete("THE RULES", "THE DOCUMENT")

    messages = json.loads(route.calls[0].request.content)["messages"]
    assert messages[0] == {"role": "system", "content": "THE RULES"}
    assert messages[1] == {"role": "user", "content": "THE DOCUMENT"}


@respx.mock
def test_json_object_mode_is_requested():
    route = respx.post(URL).mock(return_value=_ok())

    NimTextClient().complete("s", "u")

    body = json.loads(route.calls[0].request.content)
    assert body["response_format"] == {"type": "json_object"}
    assert body["stream"] is False


@respx.mock
def test_it_runs_on_the_text_model_not_the_vision_one(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "meta/llama-3.1-8b-instruct")
    monkeypatch.setenv("VISION_MODEL", "nvidia/nemotron-nano-12b-v2-vl")
    route = respx.post(URL).mock(return_value=_ok())

    NimTextClient().complete("s", "u")

    body = json.loads(route.calls[0].request.content)
    assert body["model"] == "meta/llama-3.1-8b-instruct"


@respx.mock
def test_the_reply_ceiling_is_generous_enough_for_a_long_extraction():
    """Truncation here does not look like a failure — it looks like a brief
    with fewer requirements than it has."""
    route = respx.post(URL).mock(return_value=_ok())

    NimTextClient().complete("s", "u")

    assert json.loads(route.calls[0].request.content)["max_tokens"] >= 2048


@respx.mock
def test_rate_limiting_is_retried():
    route = respx.post(URL).mock(
        side_effect=[httpx.Response(429), httpx.Response(503), _ok()]
    )

    text, _ = NimTextClient(backoff_multiplier=0).complete("s", "u")

    assert route.call_count == 3
    assert json.loads(text)["features"] == ["SSO"]


@respx.mock
def test_a_bad_request_is_not_retried():
    """A 400 means the payload is wrong; retrying only burns the rate limit."""
    route = respx.post(URL).mock(return_value=httpx.Response(400))

    with pytest.raises(httpx.HTTPStatusError):
        NimTextClient(backoff_multiplier=0).complete("s", "u")

    assert route.call_count == 1


def test_a_missing_key_names_the_variable(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="NVIDIA_API_KEY"):
        NimTextClient().complete("s", "u")


@respx.mock
def test_missing_usage_degrades_to_an_empty_dict():
    respx.post(URL).mock(return_value=_ok(usage=None))

    _, usage = NimTextClient().complete("s", "u")

    assert usage == {}


# ── live ────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_live_extraction_returns_valid_json():
    """Deselected by default. Exercises the real hop-2 call end to end."""
    from stratpoint_rag.docparse import extract

    if not config.nvidia_api_key():
        pytest.skip("no NVIDIA API key configured")

    markdown = (
        "## Page 1\n"
        "### Requirements\n"
        "- Single sign-on for staff\n"
        "- Checkout with card payments\n"
        "\n### Constraints\n"
        "- Must launch within 12 weeks\n"
        "- GDPR compliance is mandatory\n"
        "- Web and iOS only\n"
    )

    result = extract.extract_requirements(
        markdown, provenance={"pages_total": 1, "pages_parsed": 1}
    )

    assert result.complexity in ("low", "medium", "high")
    assert result.features, "the model returned no features for an explicit list"
    assert result.pages_total == 1
