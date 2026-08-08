"""Attachment bookkeeping for the sidebar — pure functions, no Streamlit.

Kept out of ``app.py`` because the rules here are exactly the ones that break
silently under Streamlit's execution model, and widgets are untestable while
plain lists are not.

Each attachment is a dict::

    {upload_id, sha256, filename, pages, parsed,
     pages_parsed, pages_via_vision, pages_failed, truncated, markdown_path}
"""

from __future__ import annotations

__all__ = ["add", "chip_label", "estimate_seconds", "find_by_hash", "remove"]

# Per-page wall clock for a vision call: ~5s typical, ~20s p95, and variance is
# endpoint load rather than payload size. The estimate only has to be honest
# enough that the user knows whether to wait.
_SECONDS_PER_PAGE = 5
_OVERHEAD_SECONDS = 3


def find_by_hash(current: list[dict], sha256: str) -> dict | None:
    """Return an already-uploaded attachment with this content hash.

    This is the rerun guard. ``st.file_uploader`` hands back the same file on
    every rerun, and Streamlit re-executes the script on every widget
    interaction — including each chat message — so without this the UI re-POSTs
    /upload on every turn.
    """
    return next((a for a in current if a.get("sha256") == sha256), None)


def add(current: list[dict], record: dict) -> list[dict]:
    """Append an attachment, replacing any entry with the same upload_id."""
    kept = [a for a in current if a.get("upload_id") != record.get("upload_id")]
    return [*kept, record]


def remove(current: list[dict], upload_id: str) -> list[dict]:
    return [a for a in current if a.get("upload_id") != upload_id]


def estimate_seconds(pages: int) -> int:
    return _OVERHEAD_SECONDS + max(1, pages) * _SECONDS_PER_PAGE


def chip_label(attachment: dict) -> str:
    """One line of provenance, e.g.::

        client-brief.pdf | 12 pages | 2 via vision | 1 failed

    The failure count is the reason this exists: it tells the user their
    scanned page 7 did not make it *before* they act on a quote built from it.
    """
    pages = attachment.get("pages", 0)
    parts = [attachment.get("filename", "upload"),
             f"{pages} page" + ("" if pages == 1 else "s")]

    if not attachment.get("parsed", True):
        parts.append("not transcribed yet")
        return " | ".join(parts)

    if attachment.get("truncated"):
        parts.append(f"first {attachment.get('pages_parsed', 0)} only")
    if attachment.get("pages_via_vision"):
        parts.append(f"{attachment['pages_via_vision']} via vision")
    if attachment.get("pages_failed"):
        parts.append(f"{len(attachment['pages_failed'])} failed")

    return " | ".join(parts)
