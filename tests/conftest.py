"""Test-wide isolation of the on-disk artifacts the app writes.

Three defaults resolve to real, in-repo paths when their env var is unset, and
the suite wrote to all three: the trace sink, the upload dir and the proposal
dir. Each is isolated here per test.

`llmops.sink` falls back to `mlflow.db` in the repo root when
MLFLOW_TRACKING_URI is unset — the same store the running app and the eval
layers read. Any test that exercises a recorded path therefore appends fixture
sessions to real telemetry: a run of the suite left 38 records under `sess1`,
`sess1234`, `alice`, `bob` and a null session id, 12 of them carrying synthetic
errors, and `/metrics` reported their error rate and cost as the product's own.

Autouse and repo-wide rather than per-test, because the leak is by omission:
only test_llmops.py set the variable, and the four other modules that reach
llmops indirectly (via the API client, the PDF tool) had no reason to know they
needed to.

**One database for the whole session, one experiment per test.** Isolating by
database instead would be the obvious move, but MLflow runs alembic migrations
when it first opens a sqlite file — measured at 12.4s. Paid per test that
touches llmops, that is minutes of suite time; paid once for the session, it is
noise. A per-test experiment name gives the same isolation, because
`sink.read_records()` only ever reads the current experiment.
"""

from __future__ import annotations

import uuid

import pytest


@pytest.fixture(scope="session")
def _llmops_db(tmp_path_factory):
    """One sqlite store for the suite — the 12.4s schema creation happens once."""
    return tmp_path_factory.mktemp("llmops") / "mlflow.db"


@pytest.fixture(autouse=True)
def log_isolated_experiment(_llmops_db, monkeypatch):
    """Point llmops at the session store, in an experiment unique to this test."""
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{_llmops_db.as_posix()}")
    monkeypatch.setenv("MLFLOW_EXPERIMENT", f"test-{uuid.uuid4().hex[:12]}")
    monkeypatch.setenv("LLMOPS_ENABLED", "1")


@pytest.fixture(autouse=True)
def _isolate_artifact_dirs(tmp_path, monkeypatch):
    """Keep the suite out of data/uploads and data/proposals.

    The API lifespan purges both on boot — by design, per the docparse and
    pdf_gen cleanup rules — so every test that stands the app up under
    TestClient deleted the real ones. Reproduced: seed a proposal, run the
    suite, and `data/proposals` is gone, which also drops the judge layer to
    SKIP for want of anything to score.

    Tests that monkeypatch `config.proposal_dir`/`upload_dir` directly still
    win; this only replaces the env-var default they fall back to.
    """
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("PROPOSAL_DIR", str(tmp_path / "proposals"))
