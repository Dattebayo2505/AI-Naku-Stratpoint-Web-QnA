"""MLflow trace store (Component #8) — one MLflow run per request.

MLflow is the observability tool the capstone spec names, and it is the *source
of truth*: `/metrics`, the UI panel and the trajectory/e2e eval layers all read
back through `read_records()`. The public surface is deliberately unchanged from
the JSONL sink it replaces — `enabled()`, `append()`, `read_records()` — so
`metrics.py`, `evaluation/traces.py` and `api/app.py` never learned where the
records live.

Backend is sqlite (`sqlite:///mlflow.db`), not `file:./mlruns`: MLflow 3.x puts
the filesystem store in maintenance mode and refuses it without an opt-out env
var, it writes ~15 files per request, and reading it walks every run directory.

**Records are JSON-encoded into params, never str()-ed.** MLflow params are
strings, and `metrics.aggregate` counts `is_grounded` for truthiness — where the
string "False" is True. A naive `str(value)` therefore reports a 100% grounded
rate forever, silently. Numeric fields become MLflow metrics so they plot over
time in `mlflow ui`; ints are restored on read, since metrics come back float.

Config:
  LLMOPS_ENABLED=0     turn telemetry off entirely (writes and reads)
  MLFLOW_TRACKING_URI  default sqlite:///mlflow.db
  MLFLOW_EXPERIMENT    default stratpoint-rag
"""

from __future__ import annotations

import json
import os
import threading

# MLflow 3.x hid the local file store behind an opt-out flag. We default to
# sqlite, but a deploy that overrides MLFLOW_TRACKING_URI to file:./mlruns
# should degrade to "works", not "raises". Set before mlflow is imported.
os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")

# Numeric fields -> MLflow metrics (plottable). Everything else -> params,
# JSON-encoded. A key in neither list is not persisted.
_METRIC_KEYS = ("latency_ms", "prompt_tokens", "completion_tokens", "total_tokens", "cost_usd")
_INT_KEYS = ("latency_ms", "prompt_tokens", "completion_tokens", "total_tokens")
_PARAM_KEYS = (
    "session_id", "path", "model", "tool_calls", "error",
    "is_grounded", "confidence", "guardrail_reason",
)
_TS_TAG = "trace_ts"

_LOCK = threading.Lock()
# Separate from _LOCK, and not reentrant-shared with it: append() holds this
# across a call to _experiment_id(), which takes _LOCK. One threading.Lock for
# both would deadlock on the first write.
_WRITE_LOCK = threading.Lock()
_experiments: dict[tuple[str, str], str] = {}  # (tracking_uri, name) -> experiment_id


def enabled() -> bool:
    return os.getenv("LLMOPS_ENABLED", "1").lower() not in ("0", "false", "no", "")


def _tracking_uri() -> str:
    return os.getenv("MLFLOW_TRACKING_URI") or "sqlite:///mlflow.db"


def _experiment_name() -> str:
    return os.getenv("MLFLOW_EXPERIMENT") or "stratpoint-rag"


def _client():
    """A tracking client for the configured URI.

    Read the env vars on every call rather than caching a configured client:
    the test suite swaps experiments per test, and a client pinned at import
    would write every test's records into whichever experiment ran first.
    """
    from mlflow.tracking import MlflowClient  # noqa: PLC0415 - keeps import cost off module load

    return MlflowClient(tracking_uri=_tracking_uri())


def _experiment_id(client) -> str:
    """Resolve (creating if needed) the current experiment id, cached per URI+name.

    Cached because resolving costs a DB round-trip and this sits on the request
    path; keyed by URI *and* name so a test that swaps either gets its own.
    """
    key = (_tracking_uri(), _experiment_name())
    with _LOCK:
        hit = _experiments.get(key)
    if hit is not None:
        return hit
    exp = client.get_experiment_by_name(key[1])
    exp_id = exp.experiment_id if exp else client.create_experiment(key[1])
    with _LOCK:
        _experiments[key] = exp_id
    return exp_id


def _encode(record: dict) -> tuple[dict[str, str], dict[str, float]]:
    """Record -> (params, metrics). Nones are omitted, not written as "None"."""
    params = {
        k: json.dumps(record[k])
        for k in _PARAM_KEYS
        if record.get(k) is not None
    }
    metrics = {
        k: float(record[k]) for k in _METRIC_KEYS if record.get(k) is not None
    }
    return params, metrics


def _decode(run) -> dict:
    """One MLflow run -> the record dict the rest of the codebase expects.

    Every key is present with an explicit None when absent, because callers
    (`metrics.aggregate`, `traces.session_error`) test `.get(k) is not None`
    and a missing key must read as "not measured", exactly like the old JSONL.
    """
    p, m = run.data.params, run.data.metrics
    rec: dict = {"ts": run.data.tags.get(_TS_TAG)}
    for k in _PARAM_KEYS:
        raw = p.get(k)
        try:
            rec[k] = json.loads(raw) if raw is not None else None
        except json.JSONDecodeError:
            rec[k] = raw  # hand-written run, or a pre-JSON record: keep the text
    for k in _METRIC_KEYS:
        val = m.get(k)
        rec[k] = None if val is None else (int(val) if k in _INT_KEYS else val)
    rec["tool_calls"] = rec.get("tool_calls") or []  # the list is never None downstream
    return rec


def append(record: dict) -> None:
    """Persist one record as an MLflow run (no-op when disabled).

    Never raises: observability must not take a request down with it. The cost
    is ~130ms of sqlite writes on the request thread — noise against a
    multi-second NIM turn.

    Writes are serialized. sqlite allows one writer at a time and returns
    "database is locked" to the losers — which `except Exception: pass` would
    swallow, silently dropping records from the store the eval layers score.
    Measured before the lock: 159 of 160 concurrent records survived.
    """
    if not enabled():
        return
    try:
        from mlflow.entities import Metric, Param, RunTag  # noqa: PLC0415

        client = _client()
        params, metrics = _encode(record)
        ts = record.get("ts") or ""
        # ponytail: one process-wide write lock, same as the JSONL sink it
        # replaces. Enough because a single uvicorn worker owns the store; if
        # this ever runs multi-process, switch the sqlite file to WAL mode.
        with _WRITE_LOCK:
            run = client.create_run(
                _experiment_id(client),
                run_name=record.get("path") or "request",
                tags={_TS_TAG: ts},
            )
            # log_batch, not per-field calls: one transaction instead of ~15.
            client.log_batch(
                run.info.run_id,
                metrics=[Metric(k, v, 0, 0) for k, v in metrics.items()],
                params=[Param(k, v) for k, v in params.items()],
                tags=[RunTag(_TS_TAG, ts)],
            )
            client.set_terminated(run.info.run_id, "FINISHED")
    except Exception:
        pass  # observability never crashes a request


def read_records(limit: int | None = None) -> list[dict]:
    """Read records back, oldest first (most-recent `limit` when given).

    Oldest-first matches what the JSONL file gave callers: `traces.load_sessions`
    re-sorts by ts anyway, but `/metrics` slices `recent[-50:][::-1]` and would
    show the oldest 50 if this returned newest-first.
    """
    if not enabled():
        return []
    try:
        client = _client()
        runs = client.search_runs(
            [_experiment_id(client)],
            order_by=["attributes.start_time ASC"],
            max_results=50000,
        )
    except Exception:
        return []  # an unreachable store is an empty dashboard, not a 500
    recs = [_decode(r) for r in runs]
    return recs[-limit:] if limit else recs
