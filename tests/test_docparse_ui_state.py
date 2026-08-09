"""ui.state — attachment lifecycle across a conversation reset."""

from __future__ import annotations

import pytest

from stratpoint_rag.ui import state


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
    return fake


@pytest.fixture(autouse=True)
def _no_proposal_calls(monkeypatch):
    """Reset also drops the session's proposals; stub it so no test here hits
    the network. Its own behaviour is covered in test_proposal_ui.py."""
    monkeypatch.setattr(state.api_client, "delete_proposals", lambda s: True)


@pytest.fixture
def deleted(monkeypatch):
    calls = []
    monkeypatch.setattr(
        state.api_client, "delete_upload", lambda s, u: calls.append((s, u)) or True
    )
    return calls


def test_init_creates_an_empty_attachment_list(session):
    state.init_session_state()

    assert session.attachments == []


def test_init_does_not_clobber_existing_attachments(session):
    state.init_session_state()
    session.attachments.append({"upload_id": "a"})

    state.init_session_state()  # Streamlit reruns this on every interaction

    assert len(session.attachments) == 1


def test_reset_clears_attachments(session, deleted):
    state.init_session_state()
    session.attachments = [{"upload_id": "a3f9c2"}]

    state.reset_conversation()

    assert session.attachments == []


def test_reset_deletes_the_uploads_server_side(session, deleted):
    """Otherwise 'Reset conversation' leaves confidential briefs on disk with a
    live id."""
    state.init_session_state()
    original_session = session.session_id
    session.attachments = [{"upload_id": "one"}, {"upload_id": "two"}]

    state.reset_conversation()

    assert deleted == [(original_session, "one"), (original_session, "two")]


def test_reset_deletes_before_rotating_the_session_id(session, deleted):
    """The delete must be scoped to the session that owns the files — sending
    the NEW session id would silently delete nothing."""
    state.init_session_state()
    old = session.session_id
    session.attachments = [{"upload_id": "one"}]

    state.reset_conversation()

    assert deleted[0][0] == old
    assert session.session_id != old


def test_reset_still_clears_messages(session, deleted):
    state.init_session_state()
    session.messages = [{"role": "user", "content": "hi"}]

    state.reset_conversation()

    assert session.messages == []


def test_reset_survives_a_failing_delete(session, monkeypatch):
    """A dead API must not strand the user in a conversation they can't reset."""
    monkeypatch.setattr(state.api_client, "delete_upload", lambda s, u: False)
    state.init_session_state()
    session.attachments = [{"upload_id": "one"}]

    state.reset_conversation()

    assert session.attachments == []
