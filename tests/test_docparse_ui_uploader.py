"""ui.app brief uploader — the confirm dialog across Streamlit's rerun cycle.

The bug this file exists for: clicking Transcribe or Cancel left the modal on
screen forever, dismissable only with the window's X. Nothing shallower than a
replayed rerun sequence catches it, because the defect *is* the sequence — the
click cleared ``pending_upload``, which re-armed the /upload guard, which
re-created ``pending_upload``, which reopened the dialog.

So this module fakes the ``streamlit`` module and drives the real
``_render_brief_uploader`` one script run at a time, modelling:

* a full script run — the whole function executes;
* ``st.rerun()`` — aborts the current run (the real one raises too);
* a click inside the modal — a *fragment* rerun: only the dialog body executes,
  with the arguments Streamlit captured when the dialog was opened.
"""

from __future__ import annotations

import hashlib
import importlib
import sys
import types
from contextlib import contextmanager

import pytest

FILE_BYTES = b"%PDF-pretend-brief"
FILE_DIGEST = hashlib.sha256(FILE_BYTES).hexdigest()
DIALOG_TITLE = "Transcribe this document?"


class _Rerun(Exception):
    """Stand-in for streamlit.runtime.scriptrunner.RerunException."""


class _SessionState(dict):
    """Streamlit's session_state supports `in`, item and attribute access."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name, value):
        self[name] = value


class _UploadedFile:
    def __init__(self, name: str, data: bytes):
        self.name = name
        self._data = data

    def getvalue(self) -> bytes:
        return self._data


class Harness:
    """The scripted browser: what is in the widget, what got clicked, what ran."""

    def __init__(self):
        self.uploaded: _UploadedFile | None = None
        self.clicks: set[str] = set()
        self.dialogs: list[str] = []
        self.dialog_args: tuple = ()
        self.uploads: list[str] = []
        self.parses: list[str] = []
        self.deletes: list[str] = []
        self.errors: list[str] = []
        self.upload_fails = False
        self._next_id = 0
        self._by_digest: dict[str, str] = {}

    # --- the API the UI talks to (sha256-cached, like the real /upload) -------
    def upload_file(self, session_id, filename, data):
        if self.upload_fails:
            raise _api_error("upload boom")
        self.uploads.append(filename)
        digest = hashlib.sha256(data).hexdigest()
        if digest not in self._by_digest:
            self._next_id += 1
            self._by_digest[digest] = f"up{self._next_id}"
        return {
            "upload_id": self._by_digest[digest],
            "sha256": digest,
            "filename": filename,
            "pages": 3,
        }

    def parse_upload(self, session_id, upload_id):
        self.parses.append(upload_id)
        return {
            "pages_total": 3,
            "pages_parsed": 3,
            "pages_via_vision": 0,
            "pages_failed": [],
            "truncated": False,
            "markdown_path": "",
        }

    def delete_upload(self, session_id, upload_id):
        self.deletes.append(upload_id)
        self._by_digest = {d: u for d, u in self._by_digest.items() if u != upload_id}
        return True

    # --- driving the script --------------------------------------------------
    def drop(self, data: bytes = FILE_BYTES, name: str = "brief.pdf") -> None:
        self.uploaded = _UploadedFile(name, data)

    def clear_widget(self) -> None:
        self.uploaded = None

    def run(self) -> list[str]:
        """One full script run. Returns the dialog titles opened during it."""
        self.clicks = set()
        self.dialogs = []
        try:
            self._app._render_brief_uploader()
        except _Rerun:
            pass
        return list(self.dialogs)

    def click(self, label: str) -> None:
        """A click inside the open modal: a fragment rerun of the dialog body."""
        assert self.dialog_args, "no dialog is open"
        self.clicks = {label}
        self.dialogs = []
        try:
            self._app._confirm_dialog(*self.dialog_args)
        except _Rerun:
            pass
        self.clicks = set()

    @property
    def attachment_ids(self) -> list[str]:
        return [a["upload_id"] for a in self._st.session_state.attachments]


def _api_error(msg):
    from stratpoint_rag.ui import api_client

    return api_client.APIError(msg)


def _fake_streamlit(harness: Harness) -> types.ModuleType:
    st = types.ModuleType("streamlit")

    def noop(*a, **kw):
        return None

    @contextmanager
    def ctx(*a, **kw):
        yield

    class Col:
        def button(self, label, **kw):
            return st.button(label, **kw)

        def caption(self, *a, **kw):
            return None

        def markdown(self, *a, **kw):
            return None

    def dialog(title):
        def decorate(fn):
            def wrapper(*args, **kwargs):
                harness.dialogs.append(title)
                harness.dialog_args = args
                return fn(*args, **kwargs)

            return wrapper

        return decorate

    def rerun(*a, **kw):
        raise _Rerun()

    st.session_state = _SessionState()
    st.dialog = dialog
    st.rerun = rerun
    st.file_uploader = lambda *a, **kw: harness.uploaded
    st.button = lambda label, **kw: label in harness.clicks
    st.error = lambda msg, *a, **kw: harness.errors.append(str(msg))
    st.columns = lambda spec, **kw: [
        Col() for _ in range(spec if isinstance(spec, int) else len(spec))
    ]
    st.cache_data = lambda *a, **kw: (lambda fn: fn)
    st.chat_message = st.spinner = st.expander = st.sidebar = ctx
    for name in (
        "set_page_config", "subheader", "write", "caption", "markdown", "warning",
        "success", "info", "title", "text_input", "toggle", "chat_input", "json",
        "download_button", "link_button", "divider",
    ):
        setattr(st, name, noop)
    return st


@pytest.fixture
def ui(monkeypatch):
    """Import ui.app against a fake streamlit, then put sys.modules back."""
    harness = Harness()
    fake = _fake_streamlit(harness)

    evicted = {
        name: mod
        for name, mod in sys.modules.items()
        if name == "streamlit" or name.startswith("stratpoint_rag.ui")
    }
    for name in evicted:
        del sys.modules[name]
    sys.modules["streamlit"] = fake
    try:
        app = importlib.import_module("stratpoint_rag.ui.app")
        monkeypatch.setattr(app.api_client, "upload_file", harness.upload_file)
        monkeypatch.setattr(app.api_client, "parse_upload", harness.parse_upload)
        monkeypatch.setattr(app.api_client, "delete_upload", harness.delete_upload)
        fake.session_state.session_id = "sess-1"
        fake.session_state.attachments = []
        harness._app = app
        harness._st = fake
        yield harness
    finally:
        for name in [
            n for n in list(sys.modules)
            if n == "streamlit" or n.startswith("stratpoint_rag.ui")
        ]:
            del sys.modules[name]
        sys.modules.update(evicted)


# --------------------------------------------------------------- opening it


def test_dropping_a_file_opens_the_confirm_dialog(ui):
    ui.drop()

    assert ui.run() == [DIALOG_TITLE]
    assert ui.uploads == ["brief.pdf"]


def test_the_dialog_is_not_reopened_by_an_unrelated_rerun(ui):
    """A chat message reruns the script; the modal must not pop back up."""
    ui.drop()
    ui.run()

    assert ui.run() == []
    assert ui.run() == []


# ------------------------------------------------------------------ the bug


def test_transcribe_closes_the_dialog_for_good(ui):
    ui.drop()
    ui.run()

    ui.click("Transcribe")

    assert ui.run() == []
    assert ui.run() == []


def test_cancel_closes_the_dialog_for_good(ui):
    ui.drop()
    ui.run()

    ui.click("Cancel")

    assert ui.run() == []
    assert ui.run() == []


def test_the_file_is_posted_to_upload_exactly_once(ui):
    """The reopen loop also re-POSTed /upload on every rerun."""
    ui.drop()
    ui.run()
    ui.click("Transcribe")
    ui.run()
    ui.run()

    assert ui.uploads == ["brief.pdf"]


def test_cancel_does_not_re_upload_the_file_it_just_deleted(ui):
    ui.drop()
    ui.run()
    ui.click("Cancel")
    ui.run()
    ui.run()

    assert ui.uploads == ["brief.pdf"]
    assert ui.deletes == ["up1"]


# ----------------------------------------------------- the work still happens


def test_transcribe_parses_the_upload_once_and_attaches_it(ui):
    ui.drop()
    ui.run()
    ui.click("Transcribe")
    ui.run()
    ui.run()

    assert ui.parses == ["up1"]
    assert ui.attachment_ids == ["up1"]


def test_cancel_deletes_the_upload_and_never_parses(ui):
    ui.drop()
    ui.run()
    ui.click("Cancel")
    ui.run()

    assert ui.parses == []
    assert ui.deletes == ["up1"]
    assert ui.attachment_ids == []


def test_an_attached_file_is_not_re_uploaded_on_later_reruns(ui):
    ui.drop()
    ui.run()
    ui.click("Transcribe")
    for _ in range(4):  # four more chat turns
        ui.run()

    assert ui.uploads == ["brief.pdf"]
    assert ui.parses == ["up1"]


# --------------------------------------------------------------- re-offering


def test_clearing_the_widget_lets_the_same_file_be_offered_again(ui):
    """Cancel is not permanent: clear the uploader and re-drop to ask again."""
    ui.drop()
    ui.run()
    ui.click("Cancel")
    ui.run()

    ui.clear_widget()
    ui.run()
    ui.drop()

    assert ui.run() == [DIALOG_TITLE]
    assert ui.uploads == ["brief.pdf", "brief.pdf"]


def test_a_different_file_is_offered_even_after_a_cancel(ui):
    ui.drop()
    ui.run()
    ui.click("Cancel")
    ui.run()

    ui.drop(b"%PDF-a-second-brief", name="other.pdf")

    assert ui.run() == [DIALOG_TITLE]
    assert ui.uploads == ["brief.pdf", "other.pdf"]


# ------------------------------------------------------------- failed upload


def test_a_failed_upload_shows_an_error_and_opens_no_dialog(ui):
    ui.upload_fails = True
    ui.drop()

    assert ui.run() == []
    assert ui.errors == ["upload boom"]


def test_a_failed_upload_is_retried_on_the_next_rerun(ui):
    ui.upload_fails = True
    ui.drop()
    ui.run()

    ui.upload_fails = False

    assert ui.run() == [DIALOG_TITLE]
