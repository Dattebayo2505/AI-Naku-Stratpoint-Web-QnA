"""JSONL trace sink (see docs/plan-llmops.md).

One JSON line per request, appended under a process-wide lock. No new deps, no
port, no external account — the whole point is that this works offline on the
LXC. Toggle with LLMOPS_ENABLED=0; path via LLMOPS_LOG_PATH.
"""

from __future__ import annotations

import json
import os
import threading

_LOCK = threading.Lock()


def _path() -> str:
    return os.getenv("LLMOPS_LOG_PATH") or "llmops_traces.jsonl"


def enabled() -> bool:
    return os.getenv("LLMOPS_ENABLED", "1").lower() not in ("0", "false", "no", "")


def append(record: dict) -> None:
    """Append one record as a JSON line (no-op when disabled)."""
    if not enabled():
        return
    line = json.dumps(record, ensure_ascii=False)
    # ponytail: one global lock — fine at PoC volume; per-shard only if this
    # ever sees real concurrent traffic (it won't).
    with _LOCK:
        with open(_path(), "a", encoding="utf-8") as f:
            f.write(line + "\n")


def read_records(limit: int | None = None) -> list[dict]:
    """Read back records (most-recent `limit`). Skips any corrupt line."""
    p = _path()
    if not os.path.exists(p):
        return []
    with _LOCK:
        with open(p, encoding="utf-8") as f:
            lines = f.readlines()
    recs: list[dict] = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            recs.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return recs[-limit:] if limit else recs
