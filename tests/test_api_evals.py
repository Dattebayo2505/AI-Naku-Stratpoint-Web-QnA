"""GET /evals — the eval table, served for the Streamlit panel.

It lives on the API rather than in the UI process because the ui container
mounts none of the volumes the evals read: no llmops trace store, no proposals,
no corpus. Running the suite inside Streamlit would report empty trajectory,
e2e and judge layers on every deployment that matters.
"""

from __future__ import annotations

import sys

from fastapi.testclient import TestClient

import stratpoint_rag.api.app  # noqa: F401  — registers the submodule in sys.modules
from stratpoint_rag.evaluation.harness import LayerResult

# The package __init__ re-exports `app` (the FastAPI instance), which shadows the
# `app` submodule attribute. sys.modules is the authoritative handle to the module.
app_module = sys.modules["stratpoint_rag.api.app"]
client = TestClient(app_module.app)


def _fake_layers():
    return [
        LayerResult("unit", "guardrails/deterministic", 20, 13, detail="7 off-policy"),
        LayerResult("cost", "cost/quote-arithmetic", 8, 8),
    ]


def test_evals_returns_one_row_per_layer(monkeypatch):
    monkeypatch.setattr(app_module, "_run_eval_layers", lambda judge: _fake_layers())

    body = client.get("/evals").json()

    assert [r["name"] for r in body["rows"]] == [
        "guardrails/deterministic", "cost/quote-arithmetic"
    ]
    row = body["rows"][0]
    assert row["passed"] == 13 and row["total"] == 20
    assert row["floor"] == 0.60
    assert row["status"] == "ok"
    assert row["detail"] == "7 off-policy"


def test_evals_reports_an_overall_verdict_and_a_timestamp(monkeypatch):
    monkeypatch.setattr(app_module, "_run_eval_layers", lambda judge: _fake_layers())

    body = client.get("/evals").json()

    assert body["ok"] is True          # nothing below its floor
    assert body["ran_at"].endswith("Z")


def test_a_layer_below_its_floor_makes_the_whole_run_not_ok(monkeypatch):
    monkeypatch.setattr(
        app_module, "_run_eval_layers",
        lambda judge: [LayerResult("cost", "cost/quote-arithmetic", 8, 1)],
    )

    body = client.get("/evals").json()

    assert body["rows"][0]["status"] == "FAIL"
    assert body["ok"] is False


def test_judge_defaults_on_and_can_be_turned_off(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        app_module, "_run_eval_layers",
        lambda judge: seen.setdefault("judge", judge) and [] or [],
    )

    client.get("/evals")
    assert seen["judge"] is True, "the judge is the layer the spec names; it runs by default"

    seen.clear()
    client.get("/evals", params={"judge": "false"})
    assert seen["judge"] is False
