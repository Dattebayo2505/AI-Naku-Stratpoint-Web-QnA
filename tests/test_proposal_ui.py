"""UI side of the proposal: state tracking, the API client, and URL handling.

The Streamlit component itself is not rendered here — Streamlit has no offline
render harness — so what is tested is everything the component depends on:
what state remembers, what the client fetches, and which host it fetches from.
"""

from __future__ import annotations

import pytest

from stratpoint_rag.ui import api_client, state
from stratpoint_rag.ui.components import proposal_download


class FakeSessionState(dict):
    """Streamlit's session_state supports both `in` and attribute access."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name, value):
        self[name] = value


@pytest.fixture
def session(monkeypatch):
    fake = FakeSessionState()
    monkeypatch.setattr(state.st, "session_state", fake)
    monkeypatch.setattr(state.api_client, "delete_upload", lambda s, u: True)
    monkeypatch.setattr(state.api_client, "delete_proposals", lambda s: True)
    return fake


def _response(url="/proposals/sess1/abc123.pdf", path="data/proposals/sess1/abc123.pdf"):
    return {
        "answer": "Here is your proposal.",
        "proposal_data": {
            "requirements": None,
            "estimation": None,
            "pdf": {
                "pdf_path": path,
                "file_size_bytes": 91_234,
                "download_url": url,
                "status": "success",
            },
        },
    }


# ── state tracking ──────────────────────────────────────────────────────────


def test_init_creates_the_proposal_trackers(session):
    state.init_session_state()

    assert session.proposal_pdf_path is None
    assert session.proposal_download_url is None


def test_init_does_not_clobber_a_tracked_proposal(session):
    """Streamlit reruns init on every widget interaction."""
    state.init_session_state()
    state.remember_proposal(_response())

    state.init_session_state()

    assert session.proposal_download_url == "/proposals/sess1/abc123.pdf"


def test_remembering_a_proposal_reports_it_as_new_once(session):
    """The caller raises a toast off this; a rerun must not pop it again."""
    state.init_session_state()

    assert state.remember_proposal(_response()) is True
    assert state.remember_proposal(_response()) is False


def test_a_different_proposal_is_new_again(session):
    state.init_session_state()
    state.remember_proposal(_response())

    assert state.remember_proposal(_response(url="/proposals/sess1/def456.pdf")) is True
    assert session.proposal_download_url == "/proposals/sess1/def456.pdf"


def test_a_response_without_a_proposal_changes_nothing(session):
    state.init_session_state()

    assert state.remember_proposal({"answer": "We offer cloud migration."}) is False
    assert session.proposal_download_url is None


def test_a_turn_that_only_scoped_the_work_is_not_a_proposal(session):
    """proposal_data is populated by the extraction and estimation tools too;
    only the pdf block means there is something to download."""
    state.init_session_state()
    response = {"proposal_data": {"requirements": {"features": ["SSO"]}, "pdf": None}}

    assert state.remember_proposal(response) is False


def test_reset_clears_the_tracked_proposal(session):
    state.init_session_state()
    state.remember_proposal(_response())

    state.reset_conversation()

    assert session.proposal_download_url is None
    assert session.proposal_pdf_path is None


def test_reset_deletes_the_proposals_server_side(session, monkeypatch):
    """A quote carries a client's name and their price; it must not outlive the
    conversation that produced it."""
    calls = []
    monkeypatch.setattr(
        state.api_client, "delete_proposals", lambda s: calls.append(s) or True
    )
    state.init_session_state()
    original = session.session_id

    state.reset_conversation()

    assert calls == [original]  # the OLD id — the new one owns no files


# ── the API client ──────────────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, status_code=200, content=b"%PDF-1.4"):
        self.status_code = status_code
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.exceptions.HTTPError(response=self)


def test_fetch_proposal_requests_the_configured_api_host(monkeypatch):
    """The URL comes back in a tool result the model has seen. Taking its host
    would let a prompt-injected brief steer the browser."""
    seen = {}

    def fake_get(url, timeout=None):
        seen["url"] = url
        return FakeResponse()

    monkeypatch.setattr(api_client.requests, "get", fake_get)

    api_client.fetch_proposal("http://evil.example/proposals/sess1/abc.pdf")

    assert seen["url"] == f"{api_client.API_BASE_URL}/proposals/sess1/abc.pdf"


def test_fetch_proposal_handles_a_relative_url(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        api_client.requests,
        "get",
        lambda url, timeout=None: seen.update(url=url) or FakeResponse(),
    )

    assert api_client.fetch_proposal("/proposals/sess1/abc.pdf") == b"%PDF-1.4"
    assert seen["url"].endswith("/proposals/sess1/abc.pdf")


def test_a_swept_proposal_is_none_not_an_exception(monkeypatch):
    """The TTL sweep and a restarted API are expected, not errors."""
    monkeypatch.setattr(
        api_client.requests, "get", lambda url, timeout=None: FakeResponse(404, b"")
    )

    assert api_client.fetch_proposal("/proposals/sess1/gone.pdf") is None


def test_a_dead_api_is_none_not_an_exception(monkeypatch):
    import requests

    def boom(url, timeout=None):
        raise requests.exceptions.ConnectionError()

    monkeypatch.setattr(api_client.requests, "get", boom)

    assert api_client.fetch_proposal("/proposals/sess1/abc.pdf") is None


def test_delete_proposals_never_raises(monkeypatch):
    import requests

    def boom(url, timeout=None):
        raise requests.exceptions.ConnectionError()

    monkeypatch.setattr(api_client.requests, "delete", boom)

    assert api_client.delete_proposals("sess1") is False


# ── the component's own parsing ─────────────────────────────────────────────


def test_the_component_finds_the_pdf_block():
    pdf = proposal_download._pdf_info(_response())

    assert pdf["download_url"] == "/proposals/sess1/abc123.pdf"


def test_the_component_ignores_a_turn_with_no_pdf():
    assert proposal_download._pdf_info({"answer": "hi"}) is None
    assert proposal_download._pdf_info({"proposal_data": {"pdf": None}}) is None


def test_the_component_accepts_the_model_shape_not_only_the_json_one():
    """The transcript replays whatever it stored; /chat returns dicts but a
    caller holding AgentResult itself is a legitimate shape too."""
    from stratpoint_rag.agent.contracts import PDFGenerationResult
    from stratpoint_rag.agent.models import ProposalData

    raw = {
        "proposal_data": ProposalData(
            pdf=PDFGenerationResult(
                pdf_path="p.pdf",
                file_size_bytes=1,
                download_url="/proposals/sess1/abc123.pdf",
            )
        )
    }

    assert proposal_download._pdf_info(raw)["download_url"].endswith("abc123.pdf")
