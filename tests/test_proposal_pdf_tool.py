"""The proposal tool joined to storage, telemetry, and the download endpoint.

Naming rules live in ``test_agent_brief_tool.py``; the print stage lives in
``test_pdf_service.py``. What is only visible here is the wiring between them:
where the file lands, what URL comes back, what /metrics records, and whether
a stalled render becomes an honest Observation or a confident lie.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stratpoint_rag import llmops, pdf_gen
from stratpoint_rag.agent import react, tools
from stratpoint_rag.pdf_gen import store as pdf_store


@pytest.fixture(autouse=True)
def proposal_root(tmp_path, monkeypatch):
    """Point the store at a temp dir — no test writes into ./data/proposals."""
    root = tmp_path / "proposals"
    monkeypatch.setattr(pdf_gen.store.config, "proposal_dir", lambda: str(root))
    return root


@pytest.fixture
def rendered(monkeypatch):
    """Stand in for Chromium; capture the HTML and record every call."""
    calls: list[dict] = []

    def fake_render(html, output_path, options=None):
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4 stand-in\n" + b"x" * 512)
        calls.append({"html": html, "path": path})
        return path

    monkeypatch.setattr(pdf_gen, "generate_pdf_from_html", fake_render)
    return calls


def _estimate() -> dict:
    return {
        "total_cost_usd": 10_000.0,
        "estimated_weeks": 8.0,
        "role_breakdown": [
            {
                "role": "Senior Engineer",
                "estimated_hours": 100.0,
                "hourly_rate": 100.0,
                "total_cost": 10_000.0,
            }
        ],
        "phase_timeline": [
            {
                "phase_name": "Phase 1: Discovery",
                "duration_weeks": 2.0,
                "milestones": ["Architecture Document"],
            }
        ],
        "summary": "8 weeks, $10,000.",
    }


# ── where it lands ──────────────────────────────────────────────────────────


def test_the_proposal_is_stored_under_its_session(proposal_root, rendered):
    result = tools.generate_proposal_pdf(
        {"estimation": _estimate(), "requirements": {}}, session_id="sess1"
    )

    path = Path(result.pdf_path)
    assert path.parent == proposal_root / "sess1"
    assert path.suffix == ".pdf"
    assert result.download_url.startswith("/proposals/sess1/")
    assert result.download_url.endswith(".pdf")


def test_the_html_twin_is_written_beside_the_pdf(proposal_root, rendered):
    """The UI previews the HTML — Chrome blocks a PDF data: URI inside
    Streamlit's sandboxed iframe."""
    result = tools.generate_proposal_pdf({"estimation": _estimate()}, session_id="sess1")

    twin = Path(result.pdf_path).with_suffix(".html")
    assert twin.is_file()
    assert "Cost &amp; Deliverable Schedule" in twin.read_text(encoding="utf-8")


def test_the_reported_size_is_the_file_on_disk(proposal_root, rendered):
    """The stub reported a hardcoded 1048576 bytes for every proposal ever
    generated, including ones it had failed to write."""
    result = tools.generate_proposal_pdf({"estimation": _estimate()}, session_id="sess1")

    assert result.file_size_bytes == Path(result.pdf_path).stat().st_size
    assert result.file_size_bytes > 0


def test_an_unsafe_session_id_falls_back_to_the_anonymous_dir(proposal_root, rendered):
    """The id reaches a path join. It is checked, not trusted — even though it
    is bound server-side and the model never types it."""
    result = tools.generate_proposal_pdf(
        {"estimation": _estimate()}, session_id="../../etc"
    )

    assert Path(result.pdf_path).parent == proposal_root / pdf_store.ANONYMOUS_SESSION


def test_an_explicit_output_path_is_honoured_without_a_session_dir(tmp_path, rendered):
    out = tmp_path / "custom" / "quote.pdf"

    result = tools.generate_proposal_pdf(
        {"estimation": _estimate(), "output_path": out.as_posix()}, session_id="sess1"
    )

    assert Path(result.pdf_path) == out
    assert out.is_file()


# ── honest failure ──────────────────────────────────────────────────────────


def test_an_empty_estimate_raises_rather_than_quoting_zero(proposal_root, rendered):
    """A $0.00 grand total that looks finished is worse than a failed tool
    call: the loop writes 'here is your proposal' either way."""
    with pytest.raises(RuntimeError, match="priced work"):
        tools.generate_proposal_pdf({"requirements": {"features": ["SSO"]}})

    assert not rendered


def test_a_render_failure_reaches_the_loop_as_an_error_observation(monkeypatch):
    def boom(html, output_path, options=None):
        raise pdf_gen.PdfRenderError("Timeout 30000ms exceeded")

    monkeypatch.setattr(pdf_gen, "generate_pdf_from_html", boom)

    with pytest.raises(RuntimeError, match="could not be rendered"):
        tools.generate_proposal_pdf({"estimation": _estimate()})


def test_the_loop_is_told_the_truth_when_a_render_fails(monkeypatch, proposal_root):
    """The wrapper's happy-path string says 'Generated Successfully'. A failure
    must not reach the model wearing it."""
    monkeypatch.setattr(
        pdf_gen,
        "generate_pdf_from_html",
        lambda *a, **k: (_ for _ in ()).throw(pdf_gen.PdfRenderError("boom")),
    )
    spec = next(
        s for s in tools.build_tool_specs(None, (None, None), "sess1")
        if s.name == "generate_proposal_pdf"
    )

    observation = react._execute_tool_with_retry(
        spec.name,
        spec.fn,
        json.dumps({"estimation": _estimate()}),
        react.get_default_tracer(),
    )

    assert "Successfully" not in observation
    assert "Error executing tool" in observation


# ── the estimate the loop already ran ───────────────────────────────────────


def test_the_tool_falls_back_to_the_captured_estimate(proposal_root, rendered):
    """The model re-calls the PDF tool having forgotten what the estimator
    returned two turns ago; the capture sink is the turn's memory of it."""
    tools.begin_capture()
    try:
        tools.estimate_cost_and_timeline({"features": ["SSO", "Checkout"]})
        result = tools.generate_proposal_pdf("make the proposal", session_id="sess1")
    finally:
        tools.end_capture()

    assert result.status == "success"
    assert "Tech Lead" in rendered[0]["html"]


def test_the_generated_pdf_is_captured_as_proposal_data(proposal_root, rendered):
    tools.begin_capture()
    try:
        tools.generate_proposal_pdf({"estimation": _estimate()}, session_id="sess1")
        captured = tools.captured_proposal_data()
    finally:
        tools.end_capture()

    assert captured.pdf is not None
    assert captured.pdf.download_url.startswith("/proposals/sess1/")


# ── telemetry ───────────────────────────────────────────────────────────────


def test_the_render_is_recorded_under_its_own_metrics_path(proposal_root, rendered, monkeypatch):
    recorded: list[dict] = []
    monkeypatch.setattr(llmops, "record", lambda p, ms, **kw: recorded.append({"path": p, **kw}))

    spec = next(
        s for s in tools.build_tool_specs(None, (None, None), "sess1")
        if s.name == "generate_proposal_pdf"
    )
    spec.fn(json.dumps({"estimation": _estimate()}))

    assert recorded[0]["path"] == "/proposals/generate"
    assert recorded[0]["session_id"] == "sess1"
    # No token fields: printing a PDF spends none, and zeros would dilute the
    # per-model averages beside it.
    assert recorded[0].get("total_tokens") is None


def test_a_failed_render_is_recorded_as_an_error(rendered, monkeypatch):
    recorded: list[dict] = []
    monkeypatch.setattr(llmops, "record", lambda p, ms, **kw: recorded.append({"path": p, **kw}))
    monkeypatch.setattr(
        pdf_gen,
        "generate_pdf_from_html",
        lambda *a, **k: (_ for _ in ()).throw(pdf_gen.PdfRenderError("boom")),
    )

    spec = next(
        s for s in tools.build_tool_specs(None, (None, None), "sess1")
        if s.name == "generate_proposal_pdf"
    )
    with pytest.raises(RuntimeError):
        spec.fn(json.dumps({"estimation": _estimate()}))

    assert recorded[0]["error"] == "RuntimeError"


# ── storage lifecycle ───────────────────────────────────────────────────────


def test_the_sweep_drops_proposals_past_their_ttl(proposal_root, rendered, monkeypatch):
    monkeypatch.setattr(pdf_gen.store.config, "proposal_ttl_seconds", lambda: 60)
    result = tools.generate_proposal_pdf({"estimation": _estimate()}, session_id="sess1")
    path = Path(result.pdf_path)

    assert pdf_store.sweep(now=path.stat().st_mtime + 10) == 0
    assert pdf_store.sweep(now=path.stat().st_mtime + 120) == 2  # .pdf and .html
    assert not (proposal_root / "sess1").exists()


def test_delete_session_drops_only_that_session(proposal_root, rendered):
    tools.generate_proposal_pdf({"estimation": _estimate()}, session_id="sess1")
    tools.generate_proposal_pdf({"estimation": _estimate()}, session_id="sess2")

    assert pdf_store.delete_session("sess1")
    assert not (proposal_root / "sess1").exists()
    assert (proposal_root / "sess2").is_dir()


@pytest.mark.parametrize("bad", ["../secrets", "a/b", "", "x" * 65])
def test_an_unsafe_id_never_becomes_a_path(bad):
    with pytest.raises(ValueError):
        pdf_store.proposal_path(bad, "abc123")
    assert pdf_store.find_proposal(bad, "abc123") is None


# ── the download endpoint ───────────────────────────────────────────────────


@pytest.fixture
def client(proposal_root):
    from stratpoint_rag.api.app import app

    return TestClient(app)


def test_the_endpoint_serves_a_generated_proposal(client, proposal_root, rendered):
    result = tools.generate_proposal_pdf({"estimation": _estimate()}, session_id="sess1")

    response = client.get(result.download_url)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-1.")


def test_the_endpoint_serves_the_html_twin_for_the_preview(client, proposal_root, rendered):
    result = tools.generate_proposal_pdf({"estimation": _estimate()}, session_id="sess1")

    response = client.get(result.download_url.replace(".pdf", ".html"))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_another_session_cannot_fetch_the_proposal(client, proposal_root, rendered):
    """Session scoping is a boundary: a quote carries a client's name and their
    price."""
    result = tools.generate_proposal_pdf({"estimation": _estimate()}, session_id="sess1")
    proposal_id = Path(result.pdf_path).stem

    assert client.get(f"/proposals/sess2/{proposal_id}.pdf").status_code == 404


def test_a_traversal_id_is_a_404_not_a_file(client, proposal_root):
    assert client.get("/proposals/sess1/..%2F..%2Fsecret.pdf").status_code == 404


def test_deleting_a_session_removes_its_proposals_over_http(client, proposal_root, rendered):
    result = tools.generate_proposal_pdf({"estimation": _estimate()}, session_id="sess1")

    assert client.delete("/proposals/sess1").json() == {"deleted": True}
    assert client.get(result.download_url).status_code == 404
