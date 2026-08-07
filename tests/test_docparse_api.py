"""Upload + parse endpoints.

Splitting upload from parse is what makes the confirmation dialog possible —
you cannot show a page count before opening the file — and it keeps PyMuPDF out
of the UI process.
"""

from __future__ import annotations

import hashlib
import sys

import pytest
from fastapi.testclient import TestClient

import stratpoint_rag.api.app  # noqa: F401 — registers the submodule in sys.modules
from stratpoint_rag.docparse.models import TranscriptionResult

app_module = sys.modules["stratpoint_rag.api.app"]
client = TestClient(app_module.app)

SESSION = "sess1234"


@pytest.fixture(autouse=True)
def uploads(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    return tmp_path / "uploads"


@pytest.fixture
def pdf_bytes():
    import pymupdf

    doc = pymupdf.open()
    for n in (1, 2, 3):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), f"Page {n} of the brief. " * 8, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def _upload(data: bytes, name="brief.pdf", session=SESSION):
    return client.post(
        "/upload",
        files={"file": (name, data, "application/pdf")},
        data={"session_id": session},
    )


# ── POST /upload ────────────────────────────────────────────────────────────


def test_upload_returns_the_page_count_without_calling_the_model(pdf_bytes):
    """Sub-second and no LLM — the dialog needs a page count before confirming."""
    r = _upload(pdf_bytes)

    assert r.status_code == 200
    body = r.json()
    assert body["pages"] == 3
    assert body["filename"] == "brief.pdf"
    assert body["sha256"] == hashlib.sha256(pdf_bytes).hexdigest()
    assert body["bytes"] == len(pdf_bytes)
    assert body["upload_id"]


def test_upload_requires_a_session_id(pdf_bytes):
    r = client.post("/upload", files={"file": ("b.pdf", pdf_bytes, "application/pdf")})
    assert r.status_code == 422


def test_upload_rejects_an_unsafe_session_id(pdf_bytes):
    r = _upload(pdf_bytes, session="../../etc")
    assert r.status_code == 400


def test_upload_rejects_a_disguised_pptx(pdf_bytes):
    """st.file_uploader's type= is a client-side filter; /upload is reachable
    without Streamlit, so content decides."""
    r = _upload(b"PK\x03\x04" + b"\x00" * 128, name="deck.pdf")

    assert r.status_code == 400
    assert "pdf" in r.json()["detail"].lower()


def test_upload_rejects_an_encrypted_pdf():
    import pymupdf

    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret")
    doc.close()

    r = _upload(data, name="locked.pdf")

    assert r.status_code == 400
    assert "password" in r.json()["detail"].lower()


def test_upload_rejects_an_oversize_file(pdf_bytes, monkeypatch):
    monkeypatch.setenv("UPLOAD_MAX_BYTES", "100")

    r = _upload(pdf_bytes)

    assert r.status_code == 413


def test_reuploading_identical_bytes_reuses_the_upload(pdf_bytes):
    """Mirrors the crawler's content_hash convention — re-parsing is free."""
    first = _upload(pdf_bytes).json()

    second = _upload(pdf_bytes).json()

    assert second["upload_id"] == first["upload_id"]
    assert second["cached"] is True
    assert first["cached"] is False


def test_upload_sweeps_stale_directories(pdf_bytes, monkeypatch):
    """No scheduler and no background thread — the sweep rides on each upload,
    which is what bounds disk on an LXC that never reboots."""
    swept = {}
    monkeypatch.setattr(app_module.store, "sweep", lambda now: swept.setdefault("now", now) or 0)

    _upload(pdf_bytes)

    assert "now" in swept, "TTL sweep did not run on upload"
    assert swept["now"] > 0  # a real clock value, supplied by the API layer


# ── POST /upload/{id}/parse ─────────────────────────────────────────────────


def _canned(**kw):
    defaults = dict(
        markdown="---\npages_total: 3\n---\n\n## Page 1\n\nBody.",
        source_file="brief.pdf",
        sha256="abc",
        pages_total=3,
        pages_parsed=3,
        pages_failed=[],
        pages_via_vision=1,
        truncated=False,
        usage={"prompt_tokens": 6431, "completion_tokens": 400, "total_tokens": 6831},
    )
    return TranscriptionResult(**{**defaults, **kw})


def test_parse_returns_provenance_and_writes_the_markdown(pdf_bytes, monkeypatch):
    monkeypatch.setattr(app_module, "transcribe_document", lambda p, **kw: _canned())
    uid = _upload(pdf_bytes).json()["upload_id"]

    r = client.post(f"/upload/{uid}/parse", params={"session_id": SESSION})

    assert r.status_code == 200
    body = r.json()
    assert body["pages_parsed"] == 3
    assert body["pages_failed"] == []
    assert body["pages_via_vision"] == 1
    from pathlib import Path

    assert Path(body["markdown_path"]).read_text(encoding="utf-8").startswith("---")


def test_parse_surfaces_failed_pages(pdf_bytes, monkeypatch):
    """'page 7 failed' belongs next to the file at upload time, not as a mangled
    Observation the LLM has to reason around mid-turn."""
    monkeypatch.setattr(
        app_module, "transcribe_document", lambda p, **kw: _canned(pages_parsed=2, pages_failed=[7])
    )
    uid = _upload(pdf_bytes).json()["upload_id"]

    body = client.post(f"/upload/{uid}/parse", params={"session_id": SESSION}).json()

    assert body["pages_failed"] == [7]


def test_parse_of_an_unknown_upload_is_404(pdf_bytes):
    r = client.post("/upload/deadbeef/parse", params={"session_id": SESSION})
    assert r.status_code == 404


def test_parse_is_cached_by_upload(pdf_bytes, monkeypatch):
    """The ReAct loop and Streamlit both re-trigger; re-running 20 vision calls
    per redundant request is what the cache exists to prevent."""
    calls = []
    monkeypatch.setattr(
        app_module, "transcribe_document", lambda p, **kw: calls.append(1) or _canned()
    )
    uid = _upload(pdf_bytes).json()["upload_id"]

    client.post(f"/upload/{uid}/parse", params={"session_id": SESSION})
    second = client.post(f"/upload/{uid}/parse", params={"session_id": SESSION})

    assert len(calls) == 1
    assert second.status_code == 200
    assert second.json()["pages_parsed"] == 3


def test_parse_maps_a_missing_key_to_503(pdf_bytes, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("No vision API key")

    monkeypatch.setattr(app_module, "transcribe_document", boom)
    uid = _upload(pdf_bytes).json()["upload_id"]

    r = client.post(f"/upload/{uid}/parse", params={"session_id": SESSION})

    assert r.status_code == 503


def test_parse_maps_an_upstream_failure_to_502(pdf_bytes, monkeypatch):
    def boom(*a, **kw):
        raise ValueError("endpoint exploded")

    monkeypatch.setattr(app_module, "transcribe_document", boom)
    uid = _upload(pdf_bytes).json()["upload_id"]

    r = client.post(f"/upload/{uid}/parse", params={"session_id": SESSION})

    assert r.status_code == 502


# ── LLMOps ──────────────────────────────────────────────────────────────────


def test_parse_is_recorded_against_the_vision_model(pdf_bytes, monkeypatch):
    """_record hardcoded rag_config.llm_model(), which would misattribute every
    parse to meta/llama-3.1-8b-instruct and corrupt per-model accounting."""
    from stratpoint_rag.docparse import config as dp_config

    seen = []
    monkeypatch.setattr(app_module.llmops, "record", lambda *a, **kw: seen.append((a, kw)))
    monkeypatch.setattr(app_module, "transcribe_document", lambda p, **kw: _canned())
    uid = _upload(pdf_bytes).json()["upload_id"]

    client.post(f"/upload/{uid}/parse", params={"session_id": SESSION})

    assert seen, "the most expensive endpoint in the system was not recorded"
    args, kwargs = seen[-1]
    assert args[0] == "/upload/parse"
    assert kwargs["model"] == dp_config.vision_model()
    assert kwargs["session_id"] == SESSION


def test_parse_records_the_tokens_it_spent(pdf_bytes, monkeypatch):
    from stratpoint_rag import llmops

    seen = []
    monkeypatch.setattr(app_module.llmops, "record", lambda *a, **kw: seen.append(kw))

    def fake(path, **kw):
        # Mirror the real transcribe_document, which accumulates into the
        # thread-local before returning so /upload/parse's pop_usage() sees it.
        result = _canned()
        llmops.add_usage(result.usage)
        return result

    monkeypatch.setattr(app_module, "transcribe_document", fake)
    uid = _upload(pdf_bytes).json()["upload_id"]

    client.post(f"/upload/{uid}/parse", params={"session_id": SESSION})

    assert seen[-1]["prompt_tokens"] == 6431
    assert seen[-1]["total_tokens"] == 6831


def test_chat_is_still_recorded_against_the_text_model(monkeypatch):
    """The shared helper must not have changed /chat's attribution."""
    from stratpoint_rag.agent import AgentResult
    from stratpoint_rag.rag import config as rag_config

    seen = []
    monkeypatch.setattr(app_module.llmops, "record", lambda *a, **kw: seen.append(kw))
    monkeypatch.setattr(app_module, "run_with_guardrails", lambda *a, **kw: AgentResult(answer="ok"))

    client.post("/chat", json={"message": "hi", "session_id": SESSION})

    assert seen[-1]["model"] == rag_config.llm_model()


# ── DELETE /upload/{id} ─────────────────────────────────────────────────────


def test_delete_removes_the_upload(pdf_bytes):
    uid = _upload(pdf_bytes).json()["upload_id"]

    r = client.request("DELETE", f"/upload/{uid}", params={"session_id": SESSION})

    assert r.status_code == 200
    assert r.json()["deleted"] is True
    assert client.post(f"/upload/{uid}/parse", params={"session_id": SESSION}).status_code == 404


def test_delete_is_idempotent():
    r = client.request("DELETE", "/upload/deadbeef", params={"session_id": SESSION})
    assert r.status_code == 200
    assert r.json()["deleted"] is False


def test_one_session_cannot_delete_anothers_upload(pdf_bytes):
    uid = _upload(pdf_bytes, session="alice").json()["upload_id"]

    client.request("DELETE", f"/upload/{uid}", params={"session_id": "bob"})

    assert client.post(f"/upload/{uid}/parse", params={"session_id": "alice"}).status_code != 404


# ── chat wiring ─────────────────────────────────────────────────────────────


def test_chat_accepts_attachments(monkeypatch):
    from stratpoint_rag.agent import AgentResult

    monkeypatch.setattr(app_module, "run_with_guardrails", lambda *a, **kw: AgentResult(answer="ok"))

    r = client.post("/chat", json={"message": "what's the timeline?", "attachments": ["a3f9c2"]})

    assert r.status_code == 200


def test_chat_rejects_a_malformed_attachments_field(monkeypatch):
    r = client.post("/chat", json={"message": "hi", "attachments": "a3f9c2"})
    assert r.status_code == 422


def test_chat_does_not_yet_forward_attachments_to_the_agent(monkeypatch):
    """Hop 1 accepts the field; hop 2 teaches the loop to use it.

    Forwarding now would TypeError against the real run_with_guardrails, which
    has no such parameter. Wiring it up needs the attachment manifest in the
    loop's context, the tool rename, and conditional registration — all hop 2.
    Delete this test when that lands.
    """
    from stratpoint_rag.agent import AgentResult

    seen = {}
    monkeypatch.setattr(
        app_module, "run_with_guardrails",
        lambda *a, **kw: seen.update(kw) or AgentResult(answer="ok"),
    )

    client.post("/chat", json={"message": "hi", "attachments": ["a3f9c2"]})

    assert "attachments" not in seen


def test_chat_call_matches_the_real_agent_signature(monkeypatch):
    """Guards the gap that let a bad kwarg through: every /chat test
    monkeypatches run_with_guardrails, so nothing else checks the real one
    would accept what the endpoint passes."""
    import inspect

    from stratpoint_rag.agent import guardrail_agent

    params = inspect.signature(guardrail_agent.run_with_guardrails).parameters
    for kwarg in ("history", "session_id", "use_nemo", "enable_reasoning"):
        assert kwarg in params, f"/chat passes {kwarg}= but the agent has no such parameter"


# ── lifecycle ───────────────────────────────────────────────────────────────


def test_startup_purges_the_upload_dir(pdf_bytes, monkeypatch):
    """The real 'delete on next run', keyed to the process that owns the files.
    Streamlit reruns dozens of times per conversation; keying cleanup to script
    execution would delete the file the user just uploaded."""
    purged = []
    monkeypatch.setattr(app_module.store, "purge_all", lambda: purged.append(1))

    with TestClient(app_module.app):
        pass

    assert purged, "uploads survived an API restart"
