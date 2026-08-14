"""Read llmops traces as sessions — shared by trajectory and e2e evals.

A session is every record with the same session_id, ordered by ts. tool_calls
are flattened across the session's records in that order. Pure stdlib; reads
what llmops.record wrote.
"""

from __future__ import annotations


def load_sessions(records: list[dict]) -> dict[str, list[dict]]:
    by_sid: dict[str, list[dict]] = {}
    for r in records:
        sid = r.get("session_id")
        if not sid:
            continue
        by_sid.setdefault(sid, []).append(r)
    for recs in by_sid.values():
        recs.sort(key=lambda r: r.get("ts") or "")
    return by_sid


def session_tool_calls(session_records: list[dict]) -> list[str]:
    calls: list[str] = []
    for r in session_records:
        calls.extend(r.get("tool_calls") or [])
    return calls


def session_error(session_records: list[dict]) -> str | None:
    """The first non-null error across the session, else None."""
    for r in session_records:
        if r.get("error"):
            return r["error"]
    return None
