import sys

from fastapi.testclient import TestClient

from stratpoint_rag.agent import AgentResult, Link, Step
import stratpoint_rag.api.app  # noqa: F401  — registers the submodule in sys.modules

# The package __init__ re-exports `app` (the FastAPI instance), which shadows the
# `app` submodule attribute. sys.modules is the authoritative handle to the module.
app_module = sys.modules["stratpoint_rag.api.app"]
client = TestClient(app_module.app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_chat_returns_serialized_agent_result(monkeypatch):
    canned = AgentResult(
        answer="We do cloud migration.",
        citations=[Link(title="Cloud", url="https://stratpoint.com/cloud")],
        resources=[],
        trace=[Step(type="answer", content="We do cloud migration.")],
    )
    monkeypatch.setattr(app_module, "run_with_guardrails", lambda *a, **kw: canned)
    r = client.post("/chat", json={"message": "do you do cloud migration?"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "We do cloud migration."
    assert body["citations"][0]["url"] == "https://stratpoint.com/cloud"


def test_chat_rejects_missing_message():
    r = client.post("/chat", json={})
    assert r.status_code == 422


def test_chat_maps_runtime_error_to_503(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("NVIDIA_API_KEY is not set")
    monkeypatch.setattr(app_module, "run_with_guardrails", boom)
    r = client.post("/chat", json={"message": "hi"})
    assert r.status_code == 503


def test_chat_maps_upstream_error_to_502(monkeypatch):
    def boom(*a, **kw):
        raise ValueError("endpoint exploded")
    monkeypatch.setattr(app_module, "run_with_guardrails", boom)
    r = client.post("/chat", json={"message": "hi"})
    assert r.status_code == 502
