"""Proposal storage: session-scoped dirs, TTL sweep, purge.

Layout::

    <PROPOSAL_DIR>/<session_id>/<proposal_id>.pdf
                               /<proposal_id>.html

Session scoping is the same boundary uploads use: one visitor must not reach
another's proposal by guessing an id, and a quote carries the client's name and
their price. The ``.html`` twin is kept beside the PDF because the UI previews
it inline — a PDF in a ``data:`` URI inside Streamlit's sandboxed iframe is
blocked by Chrome, so the preview renders the HTML the PDF was made from.

**No clock in this module.** ``sweep`` takes ``now`` from its caller, the same
rule that keeps ``docparse/store.py`` and the crawler deterministic under test.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

# One definition of the id allowlist, not two. Both modules join a
# caller-supplied id onto a path, so a fix applied to one copy and missed on the
# other is a traversal bug that passes review.
from stratpoint_rag.docparse.store import is_safe_id
from stratpoint_rag.pdf_gen import config

__all__ = [
    "ANONYMOUS_SESSION",
    "delete_session",
    "download_url",
    "find_proposal",
    "is_safe_id",
    "new_proposal_id",
    "proposal_path",
    "purge_all",
    "sweep",
]

# Used when a proposal is generated outside a chat session (a script, a test,
# the CLI). Still a directory, so the sweep and purge reach it like any other.
ANONYMOUS_SESSION = "anonymous"


def new_proposal_id() -> str:
    return uuid.uuid4().hex[:16]


def _root() -> Path:
    return Path(config.proposal_dir())


def _session_dir(session_id: str) -> Path:
    if not is_safe_id(session_id):
        raise ValueError(f"unsafe session_id: {session_id!r}")
    return _root() / session_id


def proposal_path(session_id: str, proposal_id: str, suffix: str = ".pdf") -> Path:
    """Where a proposal lives. Raises ``ValueError`` on an unsafe id."""
    if not is_safe_id(proposal_id):
        raise ValueError(f"unsafe proposal_id: {proposal_id!r}")
    if suffix not in (".pdf", ".html"):
        raise ValueError(f"unsupported proposal suffix: {suffix!r}")
    return _session_dir(session_id) / f"{proposal_id}{suffix}"


def find_proposal(session_id: str, proposal_id: str, suffix: str = ".pdf") -> Path | None:
    """Look up one proposal file, or None. Session-scoped by construction."""
    try:
        path = proposal_path(session_id, proposal_id, suffix)
    except ValueError:
        return None
    return path if path.is_file() else None


def download_url(session_id: str, proposal_id: str, suffix: str = ".pdf") -> str:
    """The API path the UI fetches. Relative: the UI knows its own API base."""
    return f"/proposals/{session_id}/{proposal_id}{suffix}"


def delete_session(session_id: str) -> bool:
    """Drop a session's proposals — wired to 'Reset conversation'."""
    if not is_safe_id(session_id):
        return False
    directory = _root() / session_id
    if not directory.is_dir():
        return False
    shutil.rmtree(directory, ignore_errors=True)
    return not directory.exists()


def purge_all() -> None:
    """Wipe every proposal. Called from the API's startup hook, beside uploads."""
    shutil.rmtree(_root(), ignore_errors=True)


def sweep(now: float) -> int:
    """Delete proposal files older than the TTL. Returns the count removed.

    ``now`` is a POSIX timestamp supplied by the caller so this module never
    reads the clock. Sweeps *files*, not directories: the two halves of one
    proposal (.pdf and .html) are written together and age together, and a
    directory mtime does not move when a file inside it is deleted.
    """
    root = _root()
    if not root.is_dir():
        return 0

    ttl = config.proposal_ttl_seconds()
    removed = 0
    for session_dir in sorted(root.iterdir()):
        if not session_dir.is_dir():
            continue
        for path in sorted(session_dir.iterdir()):
            if not path.is_file():
                continue
            try:
                age = now - path.stat().st_mtime
            except OSError:
                continue
            if age > ttl:
                path.unlink(missing_ok=True)
                if not path.exists():
                    removed += 1
        if not any(session_dir.iterdir()):
            shutil.rmtree(session_dir, ignore_errors=True)
    return removed
