"""NimVisionClient — the payload form is the whole point of these tests.

The endpoint accepts two shapes and returns HTTP 200 for both, but only one
actually routes the image to the vision encoder. Getting this wrong produces a
confident, fluent transcription of an image the model never saw. That failure
is invisible without a test pinning the wire format.
"""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from stratpoint_rag.docparse import config
from stratpoint_rag.docparse.nim import NimVisionClient


JPEG = b"\xff\xd8\xff\xe0" + b"fake jpeg bytes" * 4
URL = "https://integrate.api.nvidia.com/v1/chat/completions"


def _ok(content="### Heading\n\nBody.", usage=None):
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}}],
            "usage": usage
            or {"prompt_tokens": 6431, "completion_tokens": 412, "total_tokens": 6843},
        },
    )


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("NVIDIA_VISION_API_KEY", "nvapi-test")
    monkeypatch.delenv("VISION_MODEL", raising=False)
    monkeypatch.delenv("NVIDIA_BASE_URL", raising=False)
    # No real backoff in tests; the retry *policy* is what's under test.
    return NimVisionClient(backoff_multiplier=0.0)


# ── the wire format ─────────────────────────────────────────────────────────


@respx.mock
def test_uses_the_openai_multimodal_content_list(client):
    """The HTML-<img> form returns 200 while the base64 is tokenized as plain
    text and never reaches the vision encoder — the model then hallucinates a
    description. Token accounting shows it plainly: the same 11,268-char base64
    billed 8,058 prompt tokens under HTML-img (~1.4 chars/token, i.e. text)
    versus 1,628 under this form."""
    route = respx.post(URL).mock(return_value=_ok())

    client.describe(JPEG, "transcribe this")

    content = route.calls.last.request.read()
    body = __import__("json").loads(content)
    parts = body["messages"][-1]["content"]
    assert isinstance(parts, list), "content must be a list, not an interpolated string"
    assert parts[0]["type"] == "text"
    assert parts[1]["type"] == "image_url"
    assert "<img" not in content.decode()


@respx.mock
def test_instructions_go_in_a_system_message_not_beside_the_image(client):
    """Rules sent in the same user turn as the image get transcribed AS content.

    Observed live: an 11B vision model returned the page's real table followed
    by '### Rules' and every instruction bullet verbatim, as though the prompt
    were printed on the page. Separating the roles is what stops the leak.
    """
    route = respx.post(URL).mock(return_value=_ok())

    client.describe(JPEG, "RULES: transcribe everything exactly.")

    body = __import__("json").loads(route.calls.last.request.read())
    system, user = body["messages"]
    assert system["role"] == "system"
    assert system["content"] == "RULES: transcribe everything exactly."
    assert user["role"] == "user"
    # The user turn carries the image plus only a short nudge.
    user_text = next(p["text"] for p in user["content"] if p["type"] == "text")
    assert "RULES" not in user_text
    assert len(user_text) < 120


@respx.mock
def test_image_is_sent_as_a_jpeg_data_uri(client):
    route = respx.post(URL).mock(return_value=_ok())

    client.describe(JPEG, "prompt")

    body = __import__("json").loads(route.calls.last.request.read())
    url = body["messages"][-1]["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == JPEG


@respx.mock
def test_exactly_one_image_per_request(client):
    """'At most 1 image(s) may be provided in one prompt.' — HTTP 400, refused
    before inference."""
    route = respx.post(URL).mock(return_value=_ok())

    client.describe(JPEG, "prompt")

    body = __import__("json").loads(route.calls.last.request.read())
    parts = body["messages"][-1]["content"]
    assert sum(1 for p in parts if p.get("type") == "image_url") == 1


@respx.mock
def test_sends_the_tuning_constants(client):
    route = respx.post(URL).mock(return_value=_ok())

    client.describe(JPEG, "prompt")

    body = __import__("json").loads(route.calls.last.request.read())
    assert body["temperature"] == config.TEMPERATURE
    assert body["max_tokens"] == config.MAX_TOKENS
    assert body["model"] == config.vision_model()
    assert body["stream"] is False


@respx.mock
def test_user_turn_is_overridable_for_the_figure_pass(client):
    """The figure pass reuses this client with a different system prompt AND a
    different user turn — "Describe the pictures on this page." The default must
    stay the terse transcription turn, which is deliberately short so it cannot
    compete with the page for the model's attention."""
    route = respx.post(URL).mock(return_value=_ok())

    client.describe(JPEG, "sys", "Describe the pictures on this page.")

    body = __import__("json").loads(route.calls.last.request.read())
    parts = body["messages"][1]["content"]
    assert parts[0] == {"type": "text", "text": "Describe the pictures on this page."}
    assert parts[1]["type"] == "image_url"  # still the OpenAI multimodal form
    assert body["messages"][0]["content"] == "sys"


@respx.mock
def test_user_turn_defaults_to_the_transcription_turn(client):
    route = respx.post(URL).mock(return_value=_ok())

    client.describe(JPEG, "sys")

    body = __import__("json").loads(route.calls.last.request.read())
    assert body["messages"][1]["content"][0]["text"] == "Transcribe this page."


@respx.mock
def test_sends_no_frequency_penalty(client):
    """meta/llama-3.2-11b needed frequency_penalty=0.3 or it degenerated into a
    repetition loop on sparse image-heavy pages and ran to max_tokens. Nemotron
    does not: measured 3 runs with the penalty and 3 without over the same
    10-page scan, well-formed table separator rows came out 0/1/3 either way and
    no call ever approached the ceiling. A sampling knob with no measured effect
    is a knob whose next reader will assume it was measured."""
    route = respx.post(URL).mock(return_value=_ok())

    client.describe(JPEG, "prompt")

    body = __import__("json").loads(route.calls.last.request.read())
    assert "frequency_penalty" not in body


@respx.mock
def test_uses_the_vision_timeout_not_the_chat_one(client):
    """Observed live: under rate limiting NVIDIA throttles by DELAYING rather
    than returning 429, so tenacity never fires. A bounded per-page ceiling
    turns that hang into a recorded failed page, which is the degradation the
    rest of the design already assumes. Endpoint behaviour, not model
    behaviour — it survived the switch to nemotron."""
    route = respx.post(URL).mock(return_value=_ok())

    client.describe(JPEG, "prompt")

    assert route.calls.last.request.extensions["timeout"]["read"] == config.VISION_TIMEOUT
    assert config.VISION_TIMEOUT < config.llm_timeout()


def test_the_vision_ceiling_is_sized_for_nemotron():
    """3.5-19s per page at 1120px across six runs, so 45s is ~2.4x the slowest
    page observed — wide enough not to clip a merely slow one, tight enough that
    a wholly-stalled 40-page scan fails in 450s rather than 900s. The ceiling
    exists at all because the endpoint throttles by DELAYING rather than
    returning 429, so tenacity never fires; that is endpoint behaviour and did
    not change with the model. A 4-image probe call stalled past 300s, so it
    still fires."""
    assert config.VISION_TIMEOUT == 45


@respx.mock
def test_authorizes_with_the_vision_key(client):
    route = respx.post(URL).mock(return_value=_ok())

    client.describe(JPEG, "prompt")

    assert route.calls.last.request.headers["authorization"] == "Bearer nvapi-test"


@respx.mock
def test_falls_back_to_the_text_key(monkeypatch):
    monkeypatch.delenv("NVIDIA_VISION_API_KEY", raising=False)
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-shared")
    route = respx.post(URL).mock(return_value=_ok())

    NimVisionClient(backoff_multiplier=0.0).describe(JPEG, "prompt")

    assert route.calls.last.request.headers["authorization"] == "Bearer nvapi-shared"


def test_missing_key_raises_before_any_request(monkeypatch):
    monkeypatch.delenv("NVIDIA_VISION_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="NVIDIA_API_KEY"):
        NimVisionClient().describe(JPEG, "prompt")


# ── the return contract ─────────────────────────────────────────────────────


@respx.mock
def test_returns_content_and_usage(client):
    respx.post(URL).mock(
        return_value=_ok(
            "### Requirements\n\n| ID | Req |",
            usage={"prompt_tokens": 6431, "completion_tokens": 400, "total_tokens": 6831},
        )
    )

    markdown, usage = client.describe(JPEG, "prompt")

    assert markdown == "### Requirements\n\n| ID | Req |"
    assert usage == {"prompt_tokens": 6431, "completion_tokens": 400, "total_tokens": 6831}


@respx.mock
def test_usage_defaults_to_empty_when_absent(client):
    respx.post(URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})
    )

    _, usage = client.describe(JPEG, "prompt")

    assert usage == {}


@respx.mock
def test_never_accumulates_usage_itself(client, monkeypatch):
    """Clients return usage; the request thread records it. See clients.py."""
    from stratpoint_rag import llmops

    calls = []
    monkeypatch.setattr(llmops, "add_usage", lambda u: calls.append(u))
    respx.post(URL).mock(return_value=_ok())

    client.describe(JPEG, "prompt")

    assert calls == []


# ── retries ─────────────────────────────────────────────────────────────────


@respx.mock
def test_retries_on_429_then_succeeds(client):
    """40 requests/min per model; a 20-page parse burns 20 of them, so two
    simultaneous uploads hit the ceiling."""
    route = respx.post(URL).mock(
        side_effect=[httpx.Response(429), httpx.Response(429), _ok()]
    )

    markdown, _ = client.describe(JPEG, "prompt")

    assert markdown == "### Heading\n\nBody."
    assert route.call_count == 3


@respx.mock
def test_retries_on_server_error(client):
    route = respx.post(URL).mock(side_effect=[httpx.Response(503), _ok()])

    client.describe(JPEG, "prompt")

    assert route.call_count == 2


@respx.mock
def test_does_not_retry_a_bad_request(client):
    """400 means the payload is wrong — retrying just burns rate limit."""
    route = respx.post(URL).mock(return_value=httpx.Response(400, json={"error": "nope"}))

    with pytest.raises(httpx.HTTPStatusError):
        client.describe(JPEG, "prompt")

    assert route.call_count == 1


@respx.mock
def test_gives_up_after_the_attempt_limit(client):
    route = respx.post(URL).mock(return_value=httpx.Response(429))

    with pytest.raises(httpx.HTTPStatusError):
        client.describe(JPEG, "prompt")

    assert route.call_count == client.max_attempts


# ── live ────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_live_endpoint_accepts_the_payload_form():
    """Deselected by default. It exists so you notice when NVIDIA changes the
    payload format under you — the failure mode otherwise is a silent
    hallucination, not an error."""
    import pymupdf

    if not config.nvidia_vision_api_key():
        pytest.skip("no NVIDIA API key configured")

    doc = pymupdf.open()
    page = doc.new_page(width=300, height=200)
    page.insert_text((40, 100), "INVOICE INV-2026-00815", fontsize=16)
    pix = page.get_pixmap(dpi=96)
    jpeg = pix.tobytes(output="jpeg", jpg_quality=85)
    doc.close()

    markdown, usage = NimVisionClient().describe(
        jpeg, "Transcribe all visible text on this image exactly."
    )

    assert markdown.strip()
    assert usage.get("prompt_tokens", 0) > 0
    # ~1,601 tokens/tile + 27 overhead. Text-tokenized base64 would bill
    # thousands more for this tiny image — that gap is the regression signal.
    assert usage["prompt_tokens"] < 3000
    assert "INV-2026-00815" in markdown
