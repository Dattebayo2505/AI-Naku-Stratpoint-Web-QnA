"""ui.api_client upload helpers.

The timeouts are the point: /chat and /upload/{id}/parse have very different
latency profiles and must not share one ceiling.
"""

from __future__ import annotations

import pytest

from stratpoint_rag.ui import api_client


class FakeResponse:
    def __init__(self, payload=None, status=200):
        self._payload = payload or {}
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


@pytest.fixture
def posted(monkeypatch):
    calls = []

    def fake_post(url, **kw):
        calls.append({"url": url, **kw})
        return FakeResponse({"upload_id": "abc123", "pages": 12, "cached": False})

    monkeypatch.setattr(api_client.requests, "post", fake_post)
    return calls


def test_upload_posts_the_file_and_session(posted, tmp_path):
    api_client.upload_file("sess1", "brief.pdf", b"%PDF-1.7 data")

    call = posted[-1]
    assert call["url"].endswith("/upload")
    assert call["data"] == {"session_id": "sess1"}
    assert call["files"]["file"][0] == "brief.pdf"


def test_upload_uses_a_short_timeout(posted):
    """No model runs at upload — it must not inherit the agent's ceiling."""
    api_client.upload_file("sess1", "brief.pdf", b"%PDF-1.7")

    assert posted[-1]["timeout"] <= 60


def test_parse_gets_its_own_generous_timeout(posted):
    """A 20-page brief is ~100s sequential; the 120s chat timeout is the wrong
    ceiling for it, so parse carries its own (matching LLM_TIMEOUT)."""
    api_client.parse_upload("sess1", "abc123")

    assert posted[-1]["timeout"] >= 300


def test_parse_targets_the_upload_endpoint(posted):
    api_client.parse_upload("sess1", "abc123")

    call = posted[-1]
    assert call["url"].endswith("/upload/abc123/parse")
    assert call["params"] == {"session_id": "sess1"}


def test_chat_timeout_is_unchanged(monkeypatch):
    """Regression guard: the parse timeout must not leak into /chat."""
    calls = []
    monkeypatch.setattr(
        api_client.requests, "post",
        lambda url, **kw: calls.append(kw) or FakeResponse({"answer": "ok"}),
    )

    api_client.send_message("sess1", "hi")

    assert calls[-1]["timeout"] == 120


def test_send_message_forwards_attachments(monkeypatch):
    calls = []
    monkeypatch.setattr(
        api_client.requests, "post",
        lambda url, **kw: calls.append(kw) or FakeResponse({"answer": "ok"}),
    )

    api_client.send_message("sess1", "hi", attachments=["abc123"])

    assert calls[-1]["json"]["attachments"] == ["abc123"]


def test_send_message_omits_attachments_when_absent(monkeypatch):
    calls = []
    monkeypatch.setattr(
        api_client.requests, "post",
        lambda url, **kw: calls.append(kw) or FakeResponse({"answer": "ok"}),
    )

    api_client.send_message("sess1", "hi")

    assert "attachments" not in calls[-1]["json"]


def test_delete_upload_calls_delete(monkeypatch):
    calls = []
    monkeypatch.setattr(
        api_client.requests, "delete",
        lambda url, **kw: calls.append({"url": url, **kw}) or FakeResponse({"deleted": True}),
    )

    assert api_client.delete_upload("sess1", "abc123") is True
    assert calls[-1]["url"].endswith("/upload/abc123")
    assert calls[-1]["params"] == {"session_id": "sess1"}


def test_delete_upload_never_raises(monkeypatch):
    """Cleanup runs on reset and on '(x)'; a dead API must not break the UI."""
    import requests as real_requests

    def boom(*a, **kw):
        raise real_requests.RequestException("down")

    monkeypatch.setattr(api_client.requests, "delete", boom)

    assert api_client.delete_upload("sess1", "abc123") is False


def test_upload_surfaces_an_api_error(monkeypatch):
    import requests as real_requests

    def boom(*a, **kw):
        raise real_requests.exceptions.ConnectionError()

    monkeypatch.setattr(api_client.requests, "post", boom)

    with pytest.raises(api_client.APIError):
        api_client.upload_file("sess1", "brief.pdf", b"%PDF")


def test_parse_reports_a_timeout_in_terms_of_transcription(monkeypatch):
    import requests as real_requests

    def boom(*a, **kw):
        raise real_requests.exceptions.Timeout()

    monkeypatch.setattr(api_client.requests, "post", boom)

    with pytest.raises(api_client.APIError, match="transcri"):
        api_client.parse_upload("sess1", "abc123")
